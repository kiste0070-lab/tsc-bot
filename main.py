import os
import io
import re
import logging
import calendar
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from gtts import gTTS
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import asyncio

# 로깅 설정
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("tsc_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 추가: httpx, httpcore 모듈의 반복적인 주기적 INFO 로그 숨김
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 1. 환경 설정
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
try:
    CHAT_ID_ENV = os.getenv("CHAT_ID")
    CHAT_ID = int(CHAT_ID_ENV) if CHAT_ID_ENV else 0
except (ValueError, TypeError):
    CHAT_ID = 0

# New SDK Client
client = genai.Client(api_key=GEMINI_KEY)
MODEL_ID = "gemini-3.1-flash-lite-preview"

user_sessions = {}
stop_requested = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MONTHLY_PLAN_DIR = os.path.join(BASE_DIR, "Monthly_Plan")

def contains_hangul(text: str) -> bool:
    clean_text = text.replace("부분", "").replace("문제", "").replace("답변", "").replace(" ", "")
    return any("\uAC00" <= ch <= "\uD7A3" for ch in (clean_text or ""))

# 2. TTS 변환 및 발송 함수
async def send_voice_message(context, chat_id, text):
    try:
        tts = gTTS(text=text, lang='zh-CN')
        voice_file = io.BytesIO()
        tts.write_to_fp(voice_file)
        voice_file.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=voice_file)
    except Exception as e:
        logger.error(f"TTS 에러: {e}")

# [Monthly_Plan] 중복 확인 프로세스
def get_existing_problems():
    existing = []
    if not os.path.exists(MONTHLY_PLAN_DIR):
        return existing
    for filename in os.listdir(MONTHLY_PLAN_DIR):
        if not filename.endswith(".md") or filename.startswith("."):
            continue
        filepath = os.path.join(MONTHLY_PLAN_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            # Parse problems from the file
            # Format: ### YYYY-MM-DD\n2부분 : 문제내용\n3부분 : 문제내용\n...
            lines = content.split("\n")
            for line in lines:
                # Match pattern like "2부분 : ..." or "3부분 : ..."
                match = re.match(r'^(\d)부분\s*:\s*(.+)$', line.strip())
                if match:
                    problem_text = match.group(2).strip()
                    if problem_text:
                        existing.append(problem_text)
        except Exception as e:
            logger.warning(f"파일 읽기 오류 {filename}: {e}")
    return existing

def check_duplicate(new_problems, existing_problems):
    duplicates = []
    for new_prob in new_problems:
        new_stripped = new_prob.strip()
        for exist_prob in existing_problems:
            exist_stripped = exist_prob.strip()
            # Exact match
            if new_stripped == exist_stripped:
                duplicates.append(new_stripped)
                break
            # High similarity: one contains the other (for longer texts)
            if len(new_stripped) > 10 and len(exist_stripped) > 10:
                if new_stripped in exist_stripped or exist_stripped in new_stripped:
                    duplicates.append(new_stripped)
                    break
    return duplicates

def generate_monthly_plan(year, month):
    plan_filename = f"{year}_{month:02d}.md"
    plan_filepath = os.path.join(MONTHLY_PLAN_DIR, plan_filename)

    if os.path.exists(plan_filepath):
        logger.info(f"월간 계획 이미 존재: {plan_filename} - 스킵")
        return True

    os.makedirs(MONTHLY_PLAN_DIR, exist_ok=True)

    # Get existing problems for duplicate checking
    existing_problems = get_existing_problems()
    existing_context = ""
    if existing_problems:
        existing_context = "\n[이미 사용된 문제 목록 - 절대 중복 금지]\n" + "\n".join(existing_problems[:100])

    # Get number of days in the month
    num_days = calendar.monthrange(year, month)[1]

    prompt = f"""너는 TSC 전문 중국어 시험 문제 출제 전문가야.
{year}년 {month}월의 월간 문제 계획을 만들어줘.

[요청 사항]
- {year}년 {month}월 1일부터 {num_days}일까지 매일 Part 2, Part 3, Part 4, Part 5, Part 6 문제를 1개씩 출제해줘.
- 총 {num_days}일 × 5개 파트 = {num_days * 5}개의 문제가 필요해.
- 같은 달 내에서 문제가 절대 겹치지 않아야 해.
- 각 파트의 형식은 다음과 같아:
  2부분 : (문제 내용 - 중국어만)
  3부분 : (문제 내용 - 중국어만)
  4부분 : (문제 내용 - 중국어만)
  5부분 : (문제 내용 - 중국어만)
  6부분 : (문제 내용 - 중국어만)

[출력 형식]
### YYYY-MM-DD
2부분 : 문제내용
3부분 : 문제내용
4부분 : 문제내용
5부분 : 문제내용
6부분 : 문제내용

### YYYY-MM-DD
...

[중요 규칙]
1. 문제 내용은 반드시 중국어로만 작성해.
2. 같은 달 내에서 동일한 문제가 반복되면 안 돼.
3. 기존에 사용된 문제와 절대 중복되면 안 돼.{existing_context}
4. HSK 1~4급 수준의 단어를 주로 사용해.
5. 일상생활, 쇼핑, 여행, 학교, 가족, 취미 등 다양한 주제를 다뤄줘.
6. 각 날짜별로 ### YYYY-MM-DD 형식의 헤더를 넣어줘.
7. 다른 설명이나 인사말 없이 문제만 출력해줘.
"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"월간 계획 생성 시도 {attempt + 1}/{max_retries}: {year}년 {month}월")
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt
            )
            plan_content = response.text.strip()

            # Parse and check for duplicates
            new_problems = []
            for line in plan_content.split("\n"):
                match = re.match(r'^(\d)부분\s*:\s*(.+)$', line.strip())
                if match:
                    new_problems.append(match.group(2).strip())

            duplicates = check_duplicate(new_problems, existing_problems)
            if duplicates:
                logger.warning(f"중복 문제 발견 ({len(duplicates)}개): {duplicates[:3]}... 재생성 시도")
                prompt += f"\n\n[이전 시도에서 중복된 문제들 - 이번에는 절대 사용하지 마세요]\n" + "\n".join(duplicates)
                continue

            # Write the plan file
            with open(plan_filepath, "w", encoding="utf-8") as f:
                f.write(f"# {year}년 {month}월 월간 문제 계획\n\n")
                f.write(plan_content)
                f.write("\n")

            logger.info(f"월간 계획 생성 완료: {plan_filename} ({len(new_problems)}개 문제)")
            return True

        except Exception as e:
            logger.error(f"월간 계획 생성 오류: {e}")
            if attempt == max_retries - 1:
                return False
            continue

    logger.error(f"월간 계획 생성 실패: {year}년 {month}월 (최대 재시도 초과)")
    return False

def get_today_problems(year, month, day):
    plan_filename = f"{year}_{month:02d}.md"
    plan_filepath = os.path.join(MONTHLY_PLAN_DIR, plan_filename)

    if not os.path.exists(plan_filepath):
        return None

    try:
        with open(plan_filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Find today's section
        date_str = f"{year}-{month:02d}-{day:02d}"
        date_header = f"### {date_str}"

        # Find the start of today's section
        start_idx = content.find(date_header)
        if start_idx == -1:
            return None

        # Find the end (next ### header or end of file)
        next_header_idx = content.find("\n### ", start_idx + len(date_header))
        if next_header_idx == -1:
            section = content[start_idx:]
        else:
            section = content[start_idx:next_header_idx]

        # Parse the problems
        problems = {}
        for line in section.split("\n"):
            match = re.match(r'^(\d)부분\s*:\s*(.+)$', line.strip())
            if match:
                part_num = int(match.group(1))
                problem_text = match.group(2).strip()
                problems[part_num] = problem_text

        if len(problems) == 5:
            return problems
        else:
            logger.warning(f"오늘 문제 파싱 불완전: {len(problems)}/5개")
            return problems if problems else None

    except Exception as e:
        logger.error(f"오늘 문제 읽기 오류: {e}")
        return None

# [추가] 오답노트 저장 함수
def save_wrong_note(user_text: str, model_text: str):
    if any(cmd in user_text.replace(" ", "") for cmd in ["문제설명", "문제해석", "수업종료"]):
        return
    if not contains_hangul(model_text):
        return

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y%m")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(base_dir, "wrong_notes")
    os.makedirs(folder_path, exist_ok=True)

    file_path = os.path.join(folder_path, f"{month_str}_wrong_notes.md")

    date_header = f"## {date_str}"
    needs_header = True

    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            if date_header in f.read():
                needs_header = False

    with open(file_path, "a", encoding="utf-8") as f:
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            f.write(f"# {month_str[:4]}년 {int(month_str[4:])}월 오답노트\n\n")

        if needs_header:
            f.write(f"\n{date_header}\n\n")

        f.write(f"**🗣️ 나의 답변:**\n{user_text}\n\n")
        f.write(f"**💡 첨삭/교정:**\n{model_text}\n\n")
        f.write("---\n")

# 3. 시스템 프롬프트
def get_system_prompt(problems_text):
    return f"""
너는 TSC 전문 중국어 선생님이야. 오늘 하루는 아래 5개 문제(Part 2~6)로 수업을 진행해.

[오늘의 문제]
{problems_text}

[핵심 규칙]
1. 위의 5개 문제를 아래 형식으로 한 번에 제시해. (문제 내용은 중국어만)
2부분 : 문제
3부분 : 문제
4부분 : 문제
5부분 : 문제
6부분 : 문제

2. 사용자가 '문제설명'이라고 보내면, 제시된 문제들을 부분별로 한국어로 자세히 설명해줘.
3. 사용자가 '문제해석'이라고 보내면, 제시된 문제들을 부분별로 한국어로 해석해줘.
4. 사용자가 답변을 보낼 때, 본인이 답변하고 싶은 부분만 (예: "3부분 : 답변내용") 적어서 보낼 수 있어.
5. 사용자가 답변을 한 경우:
   - 답변한 부분에 대해서만 꼼꼼하게 한국어로 첨삭/교정해줘 (병음/예시 포함, 설명은 한국어).
   - 답변하지 않은 부분에 대해서는 HSK 1~4급 단어를 사용한 2문장 정도의 예시 답변을 작성해줘.
6. 사용자가 '수업종료'라고 보내면, 5개 문제 ALL 부분에 대한 예시 답변(HSK 1~4급, 2문장)을 작성하고 수업을 종료해.
7. 문제설명/문제해석/답변 첨삭 요청에는 '한국어'로만 답해줘. (불필요한 중국어 재질문 금지)
8. 첨삭과 예시 답변이 끝나면 "수업 종료"라고 말해.
"""

async def start_lesson(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.effective_chat.id if hasattr(context, "effective_chat") and context.effective_chat else CHAT_ID

    logger.info(f"수업 시작 (Chat ID: {chat_id})")

    now = datetime.now()
    year, month, day = now.year, now.month, now.day

    # 월간 계획 확인/생성
    generate_monthly_plan(year, month)

    # 오늘 문제 읽기
    problems = get_today_problems(year, month, day)
    if not problems:
        await context.bot.send_message(chat_id=chat_id, text=f"죄송합니다. {year}-{month:02d}-{day:02d} 일자 문제를 찾을 수 없습니다. 관리자에게 문의하세요.")
        stop_requested = True
        return

    # Format problems text
    part_names = {2: "2부분", 3: "3부분", 4: "4부분", 5: "5부분", 6: "6부분"}
    problems_text = ""
    for part in [2, 3, 4, 5, 6]:
        if part in problems:
            problems_text += f"{part_names[part]} : {problems[part]}\n"

    prompt = get_system_prompt(problems_text)

    user_sessions[chat_id] = {
        "history": [types.Content(role="user", parts=[types.Part(text=prompt)])]
    }

    chat = client.chats.create(model=MODEL_ID, history=user_sessions[chat_id]["history"])
    response = chat.send_message(
        "스몰토크나 인사말 없이, 위의 5개 문제를 지정된 형식(2부분 : 문제, 3부분 : 문제 ...)에 맞게 한 번에 제공해줘."
    )

    text_response = response.text
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    if not contains_hangul(text_response):
        await send_voice_message(context, chat_id, text_response)

    user_sessions[chat_id]["history"].append(types.Content(role="model", parts=[types.Part(text=text_response)]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_requested
    chat_id = update.effective_chat.id
    user_text = update.message.text

    is_translation_request = ("문제설명" in user_text) or ("문제해석" in user_text)

    # 사용자가 직접 '수업종료' 입력 시 처리
    if "수업종료" in user_text.replace(" ", ""):
        logger.info(f"사용자 요청으로 수업 종료 (Chat ID: {chat_id})")

        if chat_id in user_sessions:
            session = user_sessions[chat_id]
            chat = client.chats.create(model=MODEL_ID, history=session["history"])
            response = chat.send_message(
                "수업종료 명령이 입력되었습니다. 5개 문제(Part 2~6) ALL 부분에 대해 HSK 1~4급 단어를 사용한 2문장 정도의 예시 답변을 작성하고 '수업 종료'라고 말해줘."
            )
            full_text = response.text
            await update.message.reply_text(full_text)
            save_wrong_note(user_text, full_text)

        await update.message.reply_text("수업을 종료합니다. 수고하셨습니다!")
        stop_requested = True
        try:
            await context.application.stop()
            await context.application.shutdown()
        except Exception:
            pass
        return

    if chat_id not in user_sessions: return

    session = user_sessions[chat_id]
    chat = client.chats.create(model=MODEL_ID, history=session["history"])
    response = chat.send_message(update.message.text)

    full_text = response.text
    await update.message.reply_text(full_text)

    # [추가] 응답 후 오답노트 저장
    save_wrong_note(user_text, full_text)

    if "수업 종료" not in full_text:
        should_send_voice = (not is_translation_request) and (not contains_hangul(full_text))
        if should_send_voice:
            await send_voice_message(context, chat_id, full_text)
        session["history"].append(types.Content(role="user", parts=[types.Part(text=update.message.text)]))
        session["history"].append(types.Content(role="model", parts=[types.Part(text=full_text)]))
    else:
        logger.info("수업이 종료되었습니다. 봇을 정지합니다.")
        stop_requested = True
        try:
            await context.application.stop()
            await context.application.shutdown()
        except Exception:
            pass
        return

async def main():
    import sys
    logger.info("즉시 수업을 시작합니다.")

    logger.info("텔레그램 봇 초기화 중...")
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        await application.initialize()
        await application.start()

        class MockContext:
            def __init__(self, app):
                self.bot = app.bot
                self.application = app

        await start_lesson(MockContext(application))

        logger.info("봇이 활성화되었습니다. 응답을 기다립니다.")
        await application.updater.start_polling()

        while not stop_requested:
            await asyncio.sleep(1)

        try:
            await application.updater.stop()
        except Exception:
            pass
        try:
            await application.stop()
            await application.shutdown()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"봇 실행 중 에러 발생: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("봇 종료")
