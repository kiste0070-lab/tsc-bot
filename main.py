import os
import io
import re
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from gtts import gTTS
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import asyncio

from sentence_plan import (
    ensure_yearly_plan,
    get_today_sentence,
    load_anchor,
    monthly_plan_exists,
)
from text_utils import normalize_chinese_lines

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

BASE_DIR = Path(__file__).resolve().parent
STUDY_NOTES_DIR = BASE_DIR / "study_notes"


def _normalize_model_name(model_name: str) -> str:
    return (model_name or "").strip().removeprefix("models/")


def validate_configured_models():
    configured = [_normalize_model_name(m) for m in MODELS if m]
    if not configured:
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
                    x in error_msg for x in ("503", "UNAVAILABLE", "500", "INTERNAL")
                )
                if transient and attempt < max_retries - 1:
                    delay = 60 if "500" in error_msg or "INTERNAL" in error_msg else retry_delay
                    time.sleep(delay)
                elif i < len(MODELS) - 1:
                    time.sleep(300)
                    break
                else:
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
    clean = text.replace("문장", "").replace("설명", "").replace("예문", "").replace(" ", "")
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


def save_study_note(user_text: str, model_text: str):
    skip_cmds = ["문장설명", "예문보기", "학습종료", "따라말하기"]
    if any(cmd in user_text.replace(" ", "") for cmd in skip_cmds):
        return
    if not contains_hangul(model_text):
        return

    now = datetime.now()
    month_str = now.strftime("%Y%m")
    STUDY_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    file_path = STUDY_NOTES_DIR / f"{month_str}_study_notes.md"
    date_header = f"## {now.strftime('%Y-%m-%d')}"

    needs_header = True
    if file_path.exists() and date_header in file_path.read_text(encoding="utf-8"):
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
1. 수업 시작 시 오늘의 문장을 아래 형식으로 제시해 (명령어 안내 없이):
   📌 오늘의 문장 (HSK 4급)
   (중국어 문장)
   (pinyin)
   (한국어 뜻)
   (주제·사용 상황 1~2문장)

2. '문장설명' → 핵심 어휘(HSK 4급), 문법 포인트(把/被·접속어 등), 발음 주의점 (한국어)
3. '예문보기' → HSK 4급 수준 예문 2~3개 (중국어 + pinyin + 한국어)
4. '따라말하기' 또는 중국어 문장 입력 → 철자·어순·뉘앙스 피드백 (한국어)
5. '학습종료' → 오늘 문장 요약, HSK 4급 암기 팁, 복습 방법 안내 후 "학습 종료" 출력
6. 모든 중국어 한자 아래 줄에 (pinyin) 필수
7. 설명·피드백은 한국어로, 예문도 HSK 4급 범위 유지
8. 중국어 문장과 예문은 반드시 한 줄에만 작성하고, 단어 사이를 줄바꿈하지 말 것
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

    anchor = load_anchor()
    if not anchor or not monthly_plan_exists(year, month):
        await context.bot.send_message(
            chat_id=chat_id,
            text="📋 HSK 4급 연간 문장 계획을 확인·생성 중입니다. 잠시만 기다려 주세요...",
        )
        if not ensure_yearly_plan(year, month):
            await context.bot.send_message(chat_id=chat_id, text="❌ 문장 계획 준비 실패")
            session.stop_requested = True
            return

    today = get_today_sentence(year, month, day)
    if not today:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ {year}-{month:02d}-{day:02d} 문장을 찾을 수 없습니다.",
        )
        session.stop_requested = True
        return

    prompt = get_system_prompt(today)
    session.add_session(
        chat_id, [types.Content(role="user", parts=[types.Part(text=prompt)])]
    )

    response = send_chat_message_with_fallback(
        chat_id,
        "인사말 없이 오늘의 HSK 4급 문장을 지정된 형식으로 바로 제시해줘.",
    )
    text_response = normalize_chinese_lines(response.text)
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    chinese_text = today["sentence"].splitlines()[0] if today.get("sentence") else ""
    await send_voice_message(context, chat_id, chinese_text)
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
            normalized = normalize_chinese_lines(response.text)
            save_study_note(user_text, normalized)
            await update.message.reply_text(normalized)
        await update.message.reply_text("오늘 학습을 마칩니다. 수고하셨습니다!")
        await shutdown_bot(context)
        return

    chat_session = session.get_session(chat_id)
    if not chat_session:
        return

    response = send_chat_message_with_fallback(chat_id, user_text)
    full_text = normalize_chinese_lines(response.text)
    save_study_note(user_text, full_text)

    if "학습 종료" not in full_text:
        await update.message.reply_text(full_text)
        if not is_korean_cmd and not contains_hangul(full_text):
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

    # Wait for user interaction or timeout
    timeout = 600
    elapsed = 0
    while not session.stop_requested and elapsed < timeout:
        await asyncio.sleep(1)
        elapsed += 1

    if elapsed >= timeout:
        logger.info("타임아웃 — 봇 자동 종료")

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
