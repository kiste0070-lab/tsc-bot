#!/usr/bin/env python3
"""
TSC 봇 → 하루 1문장 중국어 암기 봇 마이그레이션 스크립트

사용법:
  python migrate_to_daily_sentence.py --dry-run    # 변경 미리보기
  python migrate_to_daily_sentence.py              # 실제 적용
  python migrate_to_daily_sentence.py --no-backup  # 백업 없이 적용
  python migrate_to_daily_sentence.py --convert-plans  # 기존 Monthly_Plan에서 문장 추출

변경 사항:
  - main.py: Part 2~6 시험 → HSK 4급 수준 하루 1문장 암기 학습
  - Monthly_Plan/ → Daily_Sentences/
  - wrong_notes/ → study_notes/
  - 설명서.md, GitHub Actions workflow 갱신
  - hsk_bank/ → _archive/hsk_bank/ (미사용)
"""

from __future__ import annotations

import argparse
import calendar
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP_DIR = ROOT / "_migration_backup"
ARCHIVE_DIR = ROOT / "_archive"

DATE_HEADER = re.compile(r"^###\s*(\d{4})[-\s년]*(\d{1,2})[-\s월]*(\d{1,2})")
OLD_PART_LINE = re.compile(r"^2부분\s*:\s*(.+)$")
NEW_SENTENCE_LINE = re.compile(r"^문장\s*:\s*(.+)$")


# ---------------------------------------------------------------------------
# 새 main.py
# ---------------------------------------------------------------------------
NEW_MAIN_PY = r'''import os
import io
import re
import logging
import calendar
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from gtts import gTTS
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import asyncio

log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("daily_sentence_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DATE_PATTERN = re.compile(
    r"###\s*(\d{4})[-\s년]*0?(\d{1,2})[-\s월]*0?(\d{1,2})[-\s일]*"
)
SENTENCE_PATTERN = re.compile(r"^문장\s*:\s*(.+)$")
TOPIC_PATTERN = re.compile(r"^주제\s*:\s*(.+)$")
MEMO_PATTERN = re.compile(r"^메모\s*:\s*(.+)$")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
try:
    CHAT_ID_ENV = os.getenv("CHAT_ID")
    CHAT_ID = int(CHAT_ID_ENV) if CHAT_ID_ENV else 0
except (ValueError, TypeError):
    CHAT_ID = 0

GEMINI_MODEL_1 = os.getenv("GEMINI_MODEL_PRIMARY", "gemini-2.5-flash")
GEMINI_MODEL_2 = os.getenv("GEMINI_MODEL_SECONDARY", "gemini-2.5-flash-lite")

client = genai.Client(api_key=GEMINI_KEY)
MODELS = [GEMINI_MODEL_1, GEMINI_MODEL_2]


def _normalize_model_name(model_name: str) -> str:
    return (model_name or "").strip().removeprefix("models/")


def validate_configured_models():
    configured = [_normalize_model_name(m) for m in MODELS if m]
    if not configured:
        logger.warning("설정된 Gemini 모델이 없습니다.")
        return
    try:
        available = {
            _normalize_model_name(getattr(m, "name", ""))
            for m in client.models.list()
            if getattr(m, "name", "")
        }
        for name in configured:
            if name in available:
                logger.info(f"모델 사용 가능 확인: {name}")
            else:
                logger.warning(f"설정 모델 미확인: {name}")
    except Exception as e:
        logger.warning(f"모델 목록 점검 실패: {e}")


class StudySession:
    def __init__(self):
        self.user_sessions: dict = {}
        self.stop_requested: bool = False

    def add_session(self, chat_id: int, history: list):
        self.user_sessions[chat_id] = {"history": history}

    def get_session(self, chat_id: int) -> dict | None:
        return self.user_sessions.get(chat_id)

    def add_to_history(self, chat_id: int, role: str, text: str):
        if chat_id in self.user_sessions:
            self.user_sessions[chat_id]["history"].append(
                types.Content(role=role, parts=[types.Part(text=text)])
            )


session = StudySession()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENTENCES_DIR = os.path.join(BASE_DIR, "Daily_Sentences")
STUDY_NOTES_DIR = os.path.join(BASE_DIR, "study_notes")


def send_chat_message_with_fallback(
    chat_id: int, message: str, max_retries: int = 3, retry_delay: int = 120
):
    chat_session = session.get_session(chat_id)
    history = chat_session["history"] if chat_session else []
    last_error = None

    for i, model_id in enumerate(MODELS):
        logger.info(f"사용 모델: {model_id}")
        chat = client.chats.create(model=model_id, history=history)
        for attempt in range(max_retries):
            try:
                return chat.send_message(message)
            except Exception as e:
                last_error = e
                error_msg = str(e)
                transient = any(
                    x in error_msg
                    for x in ("503", "UNAVAILABLE", "500", "INTERNAL")
                )
                if transient:
                    delay = 60 if ("500" in error_msg or "INTERNAL" in error_msg) else retry_delay
                    logger.warning(f"[{model_id}] API 에러 {attempt + 1}/{max_retries}: {error_msg}")
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                    elif i < len(MODELS) - 1:
                        time.sleep(300)
                        break
                else:
                    if i < len(MODELS) - 1:
                        time.sleep(300)
                    break
    raise last_error


async def shutdown_bot(context: ContextTypes.DEFAULT_TYPE):
    logger.info("학습이 종료되었습니다.")
    session.stop_requested = True
    try:
        await context.application.stop()
        await context.application.shutdown()
    except Exception as e:
        logger.warning(f"봇 종료 중 예외: {e}")
    sys.exit(0)


def contains_hangul(text: str) -> bool:
    clean = (
        text.replace("문장", "")
        .replace("설명", "")
        .replace("예문", "")
        .replace(" ", "")
    )
    return any("\uac00" <= ch <= "\ud7a3" for ch in (clean or ""))


async def send_voice_message(context, chat_id, text):
    try:
        tts = gTTS(text=text, lang="zh-CN")
        voice_file = io.BytesIO()
        tts.write_to_fp(voice_file)
        voice_file.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=voice_file)
    except Exception as e:
        logger.error(f"TTS 에러: {e}")


def get_existing_sentences() -> list[str]:
    existing = []
    if not os.path.exists(SENTENCES_DIR):
        return existing
    for filename in os.listdir(SENTENCES_DIR):
        if not filename.endswith(".md"):
            continue
        try:
            content = (Path(SENTENCES_DIR) / filename).read_text(encoding="utf-8")
            for line in content.splitlines():
                m = SENTENCE_PATTERN.match(line.strip())
                if m:
                    existing.append(m.group(1).strip())
        except Exception as e:
            logger.warning(f"파일 읽기 오류 {filename}: {e}")
    return existing


def check_duplicate(new_sentences: list[str], existing: list[str]) -> list[str]:
    existing_set = set(existing)
    return [s for s in new_sentences if s.strip() in existing_set]


def get_monthly_filepath(year: int, month: int) -> str:
    return os.path.join(SENTENCES_DIR, f"{year}_{month:02d}.md")


def monthly_plan_exists(year: int, month: int) -> bool:
    return os.path.exists(get_monthly_filepath(year, month))


def generate_monthly_sentences(year: int, month: int, force: bool = False) -> bool:
    filepath = get_monthly_filepath(year, month)
    if os.path.exists(filepath) and not force:
        logger.info(f"월간 문장 계획 이미 존재: {filepath}")
        return True
    if force and os.path.exists(filepath):
        os.remove(filepath)

    os.makedirs(SENTENCES_DIR, exist_ok=True)
    existing = get_existing_sentences()
    existing_context = ""
    if existing:
        existing_context = "\n[이미 사용된 문장 - 절대 중복 금지]\n" + "\n".join(existing[:120])

    num_days = calendar.monthrange(year, month)[1]
    prompt = f"""너는 중국어 회화 학습 콘텐츠 제작 전문가야.
{year}년 {month}월의 '하루 1문장' 학습 계획을 만들어줘.

[요청]
- {year}년 {month}월 1일~{num_days}일, 매일 실생활에서 자주 쓰이는 중국어 문장 1개씩
- **HSK 4급 수준** 문장 (어휘·문법 모두 HSK 4 범위, 약 10~20자)
- HSK 4급 문법 활용 권장: 把/被, 不但…而且, 虽然…但是, 越…越…, 连…都, 결과보어 등
- HSK 4급 어휘 위주, 1~3급만으로 된 너무 단순한 문장은 피할 것
- 직장, 여행, 건강, 취미, 사회생활, 감정·의견 표현 등 HSK 4에 맞는 주제
- 같은 달·기존 문장과 절대 중복 금지{existing_context}

[출력 형식 - 이 형식만 사용]
### YYYY-MM-DD
문장 : (중국어 한 문장)
주제 : (한국어로 주제 한 줄, 예: 식당 주문)
메모 : (한국어로 사용 상황 한 줄)

### YYYY-MM-DD
...

[규칙]
1. 문장 필드는 중국어만
2. 모든 문장은 HSK 4급 학습자가 이해·암기하기 적합한 난이도여야 함
3. 설명·인사말 없이 위 형식만 출력
4. 날짜 헤더는 ### YYYY-MM-DD 형식
"""

    for i, model_id in enumerate(MODELS):
        for attempt in range(3):
            try:
                logger.info(f"[{model_id}] 월간 문장 생성 시도: {year}-{month:02d}")
                response = client.models.generate_content(model=model_id, contents=prompt)
                plan_content = response.text.strip()

                new_sentences = []
                for line in plan_content.splitlines():
                    m = SENTENCE_PATTERN.match(line.strip())
                    if m:
                        new_sentences.append(m.group(1).strip())

                dupes = check_duplicate(new_sentences, existing)
                if dupes:
                    prompt += "\n\n[중복 문장 - 사용 금지]\n" + "\n".join(dupes)
                    continue

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(f"# {year}년 {month}월 하루 1문장 학습 (HSK 4급)\n\n")
                    f.write(plan_content)
                    f.write("\n")

                logger.info(f"월간 문장 생성 완료: {len(new_sentences)}개")
                return True
            except Exception as e:
                logger.error(f"[{model_id}] 생성 오류: {e}")
                if i < len(MODELS) - 1:
                    time.sleep(300)
                break
    return False


def get_today_sentence(year: int, month: int, day: int) -> dict | None:
    filepath = get_monthly_filepath(year, month)
    if not os.path.exists(filepath):
        return None
    try:
        content = Path(filepath).read_text(encoding="utf-8")
        match = DATE_PATTERN.search(
            content,
            pos=content.find(f"### {year}-{month:02d}-{day:02d}")
            if f"### {year}-{month:02d}-{day:02d}" in content
            else 0,
        )
        if not match:
            # 유연한 날짜 검색
            flex = re.compile(
                rf"###\s*{year}[-\s년]*0?{month}[-\s월]*0?{day}[-\s일]*"
            )
            m2 = flex.search(content)
            if not m2:
                return None
            start = m2.start()
        else:
            start = match.start()

        next_hdr = content.find("\n### ", start + 1)
        section = content[start:] if next_hdr == -1 else content[start:next_hdr]

        result = {}
        for line in section.splitlines():
            line = line.strip()
            for pat, key in (
                (SENTENCE_PATTERN, "sentence"),
                (TOPIC_PATTERN, "topic"),
                (MEMO_PATTERN, "memo"),
            ):
                m = pat.match(line)
                if m:
                    result[key] = m.group(1).strip()
        return result if "sentence" in result else None
    except Exception as e:
        logger.error(f"오늘 문장 읽기 오류: {e}")
        return None


def save_study_note(user_text: str, model_text: str):
    skip_cmds = ["문장설명", "예문보기", "학습종료", "따라말하기"]
    if any(cmd in user_text.replace(" ", "") for cmd in skip_cmds):
        return
    if not contains_hangul(model_text):
        return

    now = datetime.now()
    month_str = now.strftime("%Y%m")
    folder = Path(STUDY_NOTES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{month_str}_study_notes.md"
    date_header = f"## {now.strftime('%Y-%m-%d')}"

    needs_header = True
    if file_path.exists():
        if date_header in file_path.read_text(encoding="utf-8"):
            needs_header = False

    with open(file_path, "a", encoding="utf-8") as f:
        if not file_path.exists() or file_path.stat().st_size == 0:
            f.write(f"# {month_str[:4]}년 {int(month_str[4:])}월 학습 기록\n\n")
        if needs_header:
            f.write(f"\n{date_header}\n\n")
        f.write(f"**🗣️ 나의 시도:**\n{user_text}\n\n")
        f.write(f"**💡 피드백:**\n{model_text}\n\n---\n")


def get_system_prompt(today: dict) -> str:
    sentence = today["sentence"]
    topic = today.get("topic", "일상 회화")
    memo = today.get("memo", "")
    memo_line = f"\n- 사용 상황: {memo}" if memo else ""

    return f"""
너는 HSK 4급 중국어 회화 코치야. 오늘은 아래 HSK 4급 수준 문장 1개를 외우는 날이야.

[오늘의 문장]
문장 : {sentence}
주제 : {topic}{memo_line}
난이도 : HSK 4급

[진행 규칙]
1. 수업 시작 시 오늘의 문장을 아래 형식으로 제시해:
   📌 오늘의 문장 (HSK 4급)
   (중국어 문장)
   (pinyin)
   (한국어 뜻)
   (주제·사용 상황 1~2문장)

2. '문장설명' → 핵심 어휘(HSK 4급 해당 여부), 문법 포인트(把/被·접속어 등), 발음 주의점을 한국어로 설명
3. '예문보기' → HSK 4급 수준 예문 2~3개 (중국어 + pinyin + 한국어)
4. '따라말하기' 또는 사용자가 중국어로 문장을 입력 → 철자·어순·뉘앙스 피드백 (한국어)
5. '학습종료' → 오늘 문장 요약, HSK 4급 암기 팁, 내일 복습 방법 안내 후 "학습 종료" 출력
6. 모든 중국어 한자 아래 줄에 (pinyin) 필수
7. 설명·피드백은 한국어로, 예문·보충 설명도 HSK 4급 범위를 유지
"""


async def start_lesson(context: ContextTypes.DEFAULT_TYPE):
    chat_id = (
        context.effective_chat.id
        if hasattr(context, "effective_chat") and context.effective_chat
        else CHAT_ID
    )
    logger.info(f"학습 시작 (Chat ID: {chat_id})")

    now = datetime.now()
    year, month, day = now.year, now.month, now.day
    today = get_today_sentence(year, month, day)

    if not monthly_plan_exists(year, month):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📋 {year}년 {month}월 문장 리스트를 생성 중입니다...",
        )
        if not generate_monthly_sentences(year, month):
            await context.bot.send_message(chat_id=chat_id, text="❌ 월간 문장 생성 실패")
            session.stop_requested = True
            return
        today = get_today_sentence(year, month, day)

    if not today:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📋 오늘 문장을 찾을 수 없어 월간 리스트를 재생성합니다...",
        )
        if not generate_monthly_sentences(year, month, force=True):
            await context.bot.send_message(chat_id=chat_id, text="❌ 문장 준비 실패")
            session.stop_requested = True
            return
        today = get_today_sentence(year, month, day)

    if not today:
        await context.bot.send_message(chat_id=chat_id, text="❌ 오늘 문장을 읽을 수 없습니다.")
        session.stop_requested = True
        return

    prompt = get_system_prompt(today)
    session.add_session(
        chat_id, [types.Content(role="user", parts=[types.Part(text=prompt)])]
    )

    response = send_chat_message_with_fallback(
        chat_id,
        "인사말 없이 오늘의 HSK 4급 문장을 지정된 형식(📌 오늘의 문장 (HSK 4급), pinyin, 한국어 뜻, 사용 상황)으로 바로 제시해줘. "
        "마지막에 '문장설명', '예문보기', '따라말하기', '학습종료' 명령을 안내해줘.",
    )
    text_response = response.text
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    if not contains_hangul(text_response):
        await send_voice_message(context, chat_id, today["sentence"])
    session.add_to_history(chat_id, "model", text_response)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    is_korean_cmd = any(
        k in user_text for k in ("문장설명", "예문보기", "학습종료", "따라말하기")
    )

    if "학습종료" in user_text.replace(" ", ""):
        chat_session = session.get_session(chat_id)
        if chat_session:
            response = send_chat_message_with_fallback(
                chat_id,
                "학습종료. 오늘 문장 요약, 암기 팁, 복습 방법을 정리하고 마지막에 '학습 종료'라고 말해줘.",
            )
            save_study_note(user_text, response.text)
            await update.message.reply_text(response.text)
        await update.message.reply_text("오늘 학습을 마칩니다. 수고하셨습니다!")
        await shutdown_bot(context)
        return

    chat_session = session.get_session(chat_id)
    if not chat_session:
        return

    response = send_chat_message_with_fallback(chat_id, user_text)
    full_text = response.text
    save_study_note(user_text, full_text)

    if "학습 종료" not in full_text:
        await update.message.reply_text(full_text)
        if not is_korean_cmd and not contains_hangul(full_text):
            # 중국어 응답이면 TTS
            chinese_only = re.sub(r"\([^)]*\)", "", full_text).strip()
            if chinese_only:
                await send_voice_message(context, chat_id, chinese_only[:200])
        session.add_to_history(chat_id, "user", user_text)
        session.add_to_history(chat_id, "model", full_text)
    else:
        await update.message.reply_text(full_text)
        await shutdown_bot(context)


async def main():
    logger.info("HSK 4급 하루 1문장 학습을 시작합니다.")
    validate_configured_models()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    await application.start()

    class MockContext:
        def __init__(self, app):
            self.bot = app.bot
            self.application = app

    await start_lesson(MockContext(application))
    await application.updater.start_polling()

    while not session.stop_requested:
        await asyncio.sleep(1)

    for stop in (application.updater.stop, application.stop, application.shutdown):
        try:
            await stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        logger.error(f"봇 실행 오류: {e}")
        sys.exit(1)
'''

NEW_README = """# 하루 1문장 중국어 암기 봇 (HSK 4급)

매일 **HSK 4급 수준**의 실용 중국어 문장 **1개**를 외우는 텔레그램 봇입니다.  
TTS 음성, Gemini 피드백, GitHub Actions 자동 실행을 지원합니다.

---

### 1. 주요 기능

* **하루 1문장 (HSK 4급)**: `Daily_Sentences/YYYY_MM.md`에 월별로 문장이 저장됩니다.
* **HSK 4급 맞춤 생성**: 해당 월 파일이 없으면 Gemini가 HSK 4급 어휘·문법(把/被, 不但…而且, 虽然…但是 등)을 반영한 문장을 생성합니다.
* **음성 학습**: 오늘의 문장을 TTS(`zh-CN`)로 들을 수 있습니다.
* **대화형 학습**: 문장설명, 예문보기, 따라말하기, 학습종료 명령 지원
* **학습 기록**: `study_notes/YYYYMM_study_notes.md`에 피드백이 누적됩니다.
* **GitHub Actions**: 매일 한국 시간 11:59에 자동 실행

### 2. 환경 설정

```bash
pip install gTTS python-telegram-bot google-genai python-dotenv
```

`.env` 파일:
```env
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
CHAT_ID=...
```

GitHub Secrets: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `CHAT_ID`  
Workflow permissions: **Read and write permissions** 필요

### 3. Daily_Sentences 형식

```
### 2026-07-06
문장 : 虽然今天很累，但是我还是要把作业做完。
주제 : 의지·계획 표현
메모 : 피곤해도 해야 할 일을 마무리할 때
```

> 문장 난이도는 **HSK 4급** 기준입니다. 너무 단순한 1~3급 문장은 생성되지 않도록 프롬프트에 반영되어 있습니다.

### 4. 사용 방법

1. 봇이 오늘의 문장 + 병음 + 한국어 뜻을 보냅니다.
2. 음성 메시지를 먼저 듣고 따라 말해 보세요.
3. 명령어:
   - `문장설명` — 문법·발음 설명
   - `예문보기` — 활용 예문
   - `따라말하기` — 따라 쓰기 안내
   - 중국어로 문장 입력 — 첨삭 피드백
   - `학습종료` — 요약 후 종료

### 5. 로컬 실행

```bash
python main.py
```

### 6. 마이그레이션

TSC 시험형에서 전환한 경우:
```bash
python migrate_to_daily_sentence.py --convert-plans
```

이전 데이터는 `_archive/`, `_migration_backup/`에 보관됩니다.
"""

NEW_WORKFLOW = """name: Daily Chinese Sentence

on:
  schedule:
    - cron: '59 2 * * *'  # KST 11:59
  workflow_dispatch:

jobs:
  run-bot:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install gTTS python-telegram-bot google-genai python-dotenv

      - name: Run Daily Sentence Bot
        env:
          TZ: Asia/Seoul
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          CHAT_ID: ${{ secrets.CHAT_ID }}
          GEMINI_MODEL_PRIMARY: ${{ secrets.GEMINI_MODEL_PRIMARY || 'gemini-2.5-flash' }}
          GEMINI_MODEL_SECONDARY: ${{ secrets.GEMINI_MODEL_SECONDARY || 'gemini-2.5-flash-lite' }}
        run: python main.py

      - name: Commit study data
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add study_notes/ Daily_Sentences/
          git diff --cached --quiet && echo "변경 없음" || \
          git commit -m "📚 하루 1문장 학습 기록: $(date '+%Y-%m-%d')"
          git push
"""


def log(msg: str, dry_run: bool = False):
    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"{prefix}{msg}")


def backup_path(name: str, timestamp: str) -> Path:
    return BACKUP_DIR / timestamp / name


def backup_file(path: Path, timestamp: str, dry_run: bool):
    if not path.exists():
        return
    dest = backup_path(path.name, timestamp)
    log(f"백업: {path} → {dest}", dry_run)
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def backup_dir(path: Path, timestamp: str, dry_run: bool):
    if not path.exists():
        return
    dest = backup_path(path.name, timestamp)
    log(f"백업: {path}/ → {dest}/", dry_run)
    if not dry_run:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(path, dest)


def convert_monthly_plan(src: Path, dest: Path, dry_run: bool) -> int:
    """Monthly_Plan (Part 2~6) → Daily_Sentences (문장 1개, 2부분에서 추출)"""
    if not src.exists():
        return 0
    content = src.read_text(encoding="utf-8")
    blocks = re.split(r"(?=^### )", content, flags=re.MULTILINE)
    lines_out = [f"# {src.stem.replace('_', '년 ', 1)}월 하루 1문장 학습 (변환됨, HSK 4급 재생성 권장)\n"]
    count = 0

    for block in blocks:
        if not block.strip().startswith("###"):
            continue
        header = block.splitlines()[0].strip()
        sentence = None
        for line in block.splitlines()[1:]:
            m = OLD_PART_LINE.match(line.strip())
            if m:
                sentence = m.group(1).strip()
                break
        if sentence:
            lines_out.append(f"\n{header}\n문장 : {sentence}\n주제 : (변환 — 주제 미지정)\n")
            count += 1

    text = "\n".join(lines_out) + "\n"
    log(f"변환: {src.name} → {dest.name} ({count}일)", dry_run)
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return count


def archive_path(path: Path, dry_run: bool):
    if not path.exists():
        return
    target = ARCHIVE_DIR / path.name
    log(f"보관: {path} → {target}", dry_run)
    if not dry_run:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(path), str(target))


def write_text(path: Path, content: str, dry_run: bool):
    log(f"작성: {path}", dry_run)
    if not dry_run:
        path.write_text(content, encoding="utf-8")


def run_migration(args: argparse.Namespace) -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dry = args.dry_run

    log("=== HSK 4급 하루 1문장 마이그레이션 시작 ===", dry)

    targets = [
        ROOT / "main.py",
        ROOT / "설명서.md",
        ROOT / ".github" / "workflows" / "daily_lesson.yml",
    ]
    if args.backup and not dry:
        for t in targets:
            backup_file(t, timestamp, dry)
        for d in ["Monthly_Plan", "wrong_notes", "hsk_bank"]:
            backup_dir(ROOT / d, timestamp, dry)

    # 1) 핵심 파일 교체
    write_text(ROOT / "main.py", NEW_MAIN_PY, dry)
    write_text(ROOT / "설명서.md", NEW_README, dry)
    write_text(ROOT / ".github" / "workflows" / "daily_lesson.yml", NEW_WORKFLOW, dry)

    # 2) Monthly_Plan → Daily_Sentences
    monthly = ROOT / "Monthly_Plan"
    daily = ROOT / "Daily_Sentences"
    converted = 0
    if args.convert_plans and monthly.exists():
        if not dry:
            daily.mkdir(parents=True, exist_ok=True)
        for f in sorted(monthly.glob("*.md")):
            converted += convert_monthly_plan(f, daily / f.name, dry)
        log(f"총 {converted}일 문장 변환 완료", dry)
    elif not daily.exists() and not dry:
        daily.mkdir(parents=True, exist_ok=True)
        log("Daily_Sentences/ 빈 폴더 생성 (다음 실행 시 Gemini가 생성)", dry)

    # 3) wrong_notes → study_notes (이름만 변경, 내용 유지)
    wrong = ROOT / "wrong_notes"
    study = ROOT / "study_notes"
    if wrong.exists() and not study.exists():
        log(f"이름 변경: wrong_notes → study_notes", dry)
        if not dry:
            wrong.rename(study)
    elif not study.exists() and not dry:
        study.mkdir(parents=True, exist_ok=True)

    # 4) 미사용 리소스 보관
    if not args.keep_hsk_bank:
        archive_path(ROOT / "hsk_bank", dry)
    if args.archive_monthly and monthly.exists():
        archive_path(monthly, dry)

    # 5) 로그 파일 안내
    old_log = ROOT / "tsc_bot.log"
    if old_log.exists():
        log(f"기존 로그 tsc_bot.log 는 그대로 두고, 새 로그는 daily_sentence_bot.log", dry)

    log("=== 마이그레이션 완료 ===", dry)
    log("", dry)
    log("다음 단계:", dry)
    log("  1. python migrate_to_daily_sentence.py --dry-run 으로 미리 확인했는지 점검", dry)
    log("  2. .env / GitHub Secrets 확인", dry)
    log("  3. python main.py 로 로컬 테스트", dry)
    log("  4. git add -A && git commit && git push", dry)
    if args.backup:
        log(f"  백업 위치: {BACKUP_DIR / timestamp}", dry)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="TSC 봇을 HSK 4급 하루 1문장 중국어 암기 봇으로 전환합니다."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일을 실제로 쓰지 않고 변경 목록만 출력",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="기존 파일 백업 생략",
    )
    parser.add_argument(
        "--convert-plans",
        action="store_true",
        help="Monthly_Plan의 2부분 문장을 Daily_Sentences로 변환",
    )
    parser.add_argument(
        "--archive-monthly",
        action="store_true",
        help="변환 후 Monthly_Plan을 _archive/로 이동",
    )
    parser.add_argument(
        "--keep-hsk-bank",
        action="store_true",
        help="hsk_bank 폴더를 보관하지 않음",
    )
    args = parser.parse_args()
    args.backup = not args.no_backup
    return run_migration(args)


if __name__ == "__main__":
    sys.exit(main())
