import os
import io
import re
import logging
import calendar
import sys
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from gtts import gTTS
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import asyncio

# ============================================================
# 리팩토링: GitHub Actions 일일 실행에 최적화
# - Polling 제거: 수업 시작 후 즉시 종료 대신 사용자 응답 대기
# - 정규식 캐싱: 반복 컴파일 방지
# - 중복 코드 함수화
# - 명확한 exit code
# ============================================================

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

# ============================================================
# 정규식 캐싱 (성능 최적화)
# ============================================================
PART_PATTERN = re.compile(r'^(\d)부분\s*:\s*(.+)$')
HSK_EVAL_PATTERN = re.compile(r'\[HSK_EVAL\]종합:([\d.]+)\|단어:([\d.]+)\|문법:([\d.]+)\[/HSK_EVAL\]')
MISTAKE_PATTERN = re.compile(r'\[자주 틀리는 표현\](.*?)(?:\n|$)')

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

# ============================================================
# 상태 관리: 클래스 기반 (global 변수 제거)
# ============================================================
class TSCSession:
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

session = TSCSession()

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
            # Parse problems from the file (정규식 캐시 사용)
            lines = content.split("\n")
            for line in lines:
                match = PART_PATTERN.match(line.strip())
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

            # Parse and check for duplicates (정규식 캐시 사용)
            new_problems = []
            for line in plan_content.split("\n"):
                match = PART_PATTERN.match(line.strip())
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

        # Parse the problems (정규식 캐시 사용)
        problems = {}
        for line in section.split("\n"):
            match = PART_PATTERN.match(line.strip())
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

def parse_hsk_eval(text: str):
    """[HSK_EVAL]종합:X.X|단어:X.X|문법:X.X[/HSK_EVAL] 태그를 파싱"""
    match = HSK_EVAL_PATTERN.search(text)
    if match:
        return {
            "종합": match.group(1),
            "단어": match.group(2),
            "문법": match.group(3)
        }
    return None

def parse_frequent_mistake(text: str):
    """[자주 틀리는 표현] 내용 추출"""
    match = MISTAKE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return None

def strip_hsk_eval(text: str):
    """응답에서 HSK_EVAL 태그를 제거"""
    return HSK_EVAL_PATTERN.sub('', text).strip()

def strip_frequent_mistake(text: str):
    """응답에서 자주 틀리는 표현 태그를 제거"""
    return MISTAKE_PATTERN.sub('', text, flags=re.DOTALL).strip()


def get_today_wrong_notes():
    """오늘의 오답노트를 가져옵니다."""
    now = datetime.now()
    month_str = now.strftime("%Y%m")
    file_path = os.path.join(BASE_DIR, "wrong_notes", f"{month_str}_wrong_notes.md")
    
    if not os.path.exists(file_path):
        return ""
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

# 3. 시스템 프롬프트
def get_system_prompt(problems_text, today_wrong_notes=""):
    wrong_notes_section = ""
    if today_wrong_notes:
        wrong_notes_section = f"""

[오늘의 오답 참고]
오늘의 수업에서 사용자가 다음과 같이 답변했고, 첨삭을 받았습니다:
{today_wrong_notes}

위 오답 내용을 참조하여 사용자가 자주 틀리는 표현이나 문법 패턴을 파악하고, 수업 종료 시 [자주 틀리는 표현] 형태로 한 줄 정도 제공해주세요.
예: [자주 틀리는 표현] 和(hé)와 함께 쓰는 표현을 자주 잊으시네요.
"""

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
6. 사용자가 '수업종료'라고 보내면, 5개 문제 ALL 부분에 대해 HSK 1~4급 단어를 사용한 2문장 정도의 예시 답변을 작성하고 수업을 종료해.
7. 문제설명/문제해석/답변 첨삭 요청에는 '한국어'로만 답해줘. (불필요한 중국어 재질문 금지)
8. 첨삭과 예시 답변이 끝나면 "수업 종료"라고 말해.

[HSK 레벨 평가 규칙]
- 사용자의 중국어 답변을 분석하여 HSK 레벨을 평가해줘.
- 평가 기준:
  * 단어: 사용한 어휘의 난이도 (HSK 1급=초급 ~ 6급=고급)
  * 문법: 사용한 문장 구조의 복잡도 (HSK 1급=단순문 ~ 6급=복합문/성어)
  * 종합: 단어와 문법의 가중 평균 (단어 50% + 문법 50%)
- 등급은 소수점 첫째자리까지 표시 (예: 3.2, 4.5)
- 수업 종료 시, 마지막 줄에 반드시 다음 형식으로 HSK 평가 결과를 포함해줘:
  [HSK_EVAL]종합:X.X|단어:X.X|문법:X.X[/HSK_EVAL]
- 예시: [HSK_EVAL]종합:3.2|단어:3.5|문법:3.0[/HSK_EVAL]
{wrong_notes_section}
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
        session.stop_requested = True
        return

    # Format problems text
    part_names = {2: "2부분", 3: "3부분", 4: "4부분", 5: "5부분", 6: "6부분"}
    problems_text = ""
    for part in [2, 3, 4, 5, 6]:
        if part in problems:
            problems_text += f"{part_names[part]} : {problems[part]}\n"

    # 오늘의 오답노트 가져오기
    today_wrong_notes = get_today_wrong_notes()
    prompt = get_system_prompt(problems_text, today_wrong_notes)

    session.add_session(chat_id, [types.Content(role="user", parts=[types.Part(text=prompt)])])

    chat = client.chats.create(model=MODEL_ID, history=session.get_session(chat_id)["history"])
    response = chat.send_message(
        "스몰토크나 인사말 없이, 위의 5개 문제를 지정된 형식(2부분 : 문제, 3부분 : 문제 ...)에 맞게 한 번에 제공해줘."
    )

    text_response = response.text
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    if not contains_hangul(text_response):
        await send_voice_message(context, chat_id, text_response)

    session.add_to_history(chat_id, "model", text_response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    is_translation_request = ("문제설명" in user_text) or ("문제해석" in user_text)

    # 사용자가 직접 '수업종료' 입력 시 처리
    if "수업종료" in user_text.replace(" ", ""):
        logger.info(f"사용자 요청으로 수업 종료 (Chat ID: {chat_id})")

        chat_session = session.get_session(chat_id)
        if chat_session:
            chat = client.chats.create(model=MODEL_ID, history=chat_session["history"])
            response = chat.send_message(
                "수업종료 명령이 입력되었습니다. 5개 문제(Part 2~6) ALL 부분에 대해 HSK 1~4급 단어를 사용한 2문장 정도의 예시 답변을 작성하고 '수업 종료'라고 말해줘. "
                "마지막 줄에 반드시 [HSK_EVAL]종합:X.X|단어:X.X|문법:X.X[/HSK_EVAL] 형식으로 HSK 레벨 평가를 포함해줘."
            )
            full_text = response.text
            save_wrong_note(user_text, full_text)

            # HSK 평가 결과 파싱 및 표시
            hsk_eval = parse_hsk_eval(full_text)
            frequent_mistake = parse_frequent_mistake(full_text)
            clean_text = strip_hsk_eval(full_text)
            clean_text = strip_frequent_mistake(clean_text)

            await update.message.reply_text(clean_text)

            if hsk_eval:
                hsk_msg = (
                    f"🎓 수업 종료되었습니다.\n\n"
                    f"📊 종합: HSK {hsk_eval['종합']}등급\n"
                    f"📖 단어: HSK {hsk_eval['단어']}등급\n"
                    f"📝 문법: HSK {hsk_eval['문법']}등급"
                )
                await update.message.reply_text(hsk_msg)

            if frequent_mistake:
                mistake_msg = f"[자주 틀리는 표현] {frequent_mistake}"
                await update.message.reply_text(mistake_msg)

        await update.message.reply_text("수업을 종료합니다. 수고하셨습니다!")
        session.stop_requested = True
        try:
            await context.application.stop()
            await context.application.shutdown()
        except Exception:
            pass
        return

    chat_session = session.get_session(chat_id)
    if not chat_session: return

    chat = client.chats.create(model=MODEL_ID, history=chat_session["history"])
    response = chat.send_message(update.message.text)

    full_text = response.text
    await update.message.reply_text(full_text)

    # 응답 후 오답노트 저장
    save_wrong_note(user_text, full_text)

    if "수업 종료" not in full_text:
        should_send_voice = (not is_translation_request) and (not contains_hangul(full_text))
        if should_send_voice:
            await send_voice_message(context, chat_id, full_text)
        session.add_to_history(chat_id, "user", update.message.text)
        session.add_to_history(chat_id, "model", full_text)
    else:
        # 봇 응답에 "수업 종료"가 포함되면 자동으로 종료 (사용자 입력 대기 안 함)
        logger.info("수업 종료 응답 감지 - 봇을 정지합니다.")
        
        # HSK 평가 결과 파싱 및 표시 (중복 코드 함수화)
        hsk_eval = parse_hsk_eval(full_text)
        frequent_mistake = parse_frequent_mistake(full_text)
        clean_text = strip_hsk_eval(full_text)
        clean_text = strip_frequent_mistake(clean_text)

        await update.message.reply_text(clean_text)

        if hsk_eval:
            hsk_msg = (
                f"🎓 수업 종료되었습니다.\n\n"
                f"📊 종합: HSK {hsk_eval['종합']}등급\n"
                f"📖 단어: HSK {hsk_eval['단어']}등급\n"
                f"📝 문법: HSK {hsk_eval['문법']}등급"
            )
            await update.message.reply_text(hsk_msg)

        if frequent_mistake:
            mistake_msg = f"[자주 틀리는 표현] {frequent_mistake}"
            await update.message.reply_text(mistake_msg)

        logger.info("수업이 종료되었습니다. 봇을 정지합니다.")
        session.stop_requested = True
        try:
            await context.application.stop()
            await context.application.shutdown()
        except Exception:
            pass
        return


async def main():
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

        while not session.stop_requested:
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
        sys.exit(1)  # 에러 시 exit code 1 반환

if __name__ == "__main__":
    try:
        asyncio.run(main())
        sys.exit(0)  # 정상 종료 시 exit code 0
    except KeyboardInterrupt:
        logger.info("봇 종료")
        sys.exit(0)
