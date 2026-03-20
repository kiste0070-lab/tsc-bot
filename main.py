import os
import io
import logging
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
MODEL_ID = "gemini-3.1-flash-lite-preview" # 최신 Flash Lite 모델 사용

user_sessions = {}
stop_requested = False

def contains_hangul(text: str) -> bool:
    # 한글(가~힣) 포함 여부로 TTS 필요 여부를 판정한다.
    return any("\uAC00" <= ch <= "\uD7A3" for ch in (text or ""))

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

# [추가] 오답노트 저장 함수
def save_wrong_note(user_text: str, model_text: str):
    # 특수 명령어는 오답노트 저장 제외
    if any(cmd in user_text.replace(" ", "") for cmd in ["문제설명", "문제해석", "수업종료"]):
        return
        
    # 모델 응답에 교정/첨삭(한국어)이 포함되지 않은 순수 문제 제시는 제외
    if not contains_hangul(model_text):
        return

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y%m")
    
    # 스크립트 위치 기준으로 wrong_notes 폴더 생성
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(base_dir, "wrong_notes")
    os.makedirs(folder_path, exist_ok=True)
    
    file_path = os.path.join(folder_path, f"{month_str}_wrong_notes.md")
    
    date_header = f"## {date_str}"
    needs_header = True
    
    # 📝 기존 파일이 있고, 오늘 날짜 헤더가 있는지 확인
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            if date_header in f.read():
                needs_header = False
                
    with open(file_path, "a", encoding="utf-8") as f:
        # 파일이 처음 생성되는 거라면 타이틀 추가
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            f.write(f"# {month_str[:4]}년 {int(month_str[4:])}월 오답노트\n\n")
        
        if needs_header:
            f.write(f"\n{date_header}\n\n")
            
        f.write(f"**🗣️ 나의 답변:**\n{user_text}\n\n")
        f.write(f"**💡 첨삭/교정:**\n{model_text}\n\n")
        f.write("---\n")

# 3. 시스템 프롬프트 (오답노트 부분 제거)
def get_system_prompt():
    return """
    너는 TSC 전문 중국어 선생님이야. Part 1은 생략하고 Part 2~7을 집중 훈련시켜.
    
    [핵심 규칙]
    1. 일반 수업 진행에서 '질문(문제 제시)'은 반드시 중국어로만 먼저 제시해. 인사말/소개/설명/한국어/번호 라벨(예: '문제:')도 금지해.
    2. 사용자가 '문제설명'이라고 보내면, 직전에 나온 중국어 문제를 한국어로만 자세히 설명해줘.
    3. 사용자가 '문제해석'이라고 보내면, 직전에 나온 중국어 문제를 한국어로만 해석해줘.
    4. 사용자가 답변을 한 뒤 제공하는 '답변 첨삭/교정'은 반드시 한국어로만 작성해줘(병음/예시는 넣어도 되지만 설명은 한국어로).
    5. 문제설명/문제해석/답변 첨삭 요청에는 '한국어'로만 답해줘. (불필요한 중국어 재질문 금지)
    6. 10문제 완료 시 "수업 종료"라고 말해.
    """

async def start_lesson(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.effective_chat.id if hasattr(context, "effective_chat") and context.effective_chat else CHAT_ID
    
    logger.info(f"수업 시작 (Chat ID: {chat_id})")
    prompt = get_system_prompt()
    
    user_sessions[chat_id] = {
        "history": [types.Content(role="user", parts=[types.Part(text=prompt)])]
    }
    
    chat = client.chats.create(model=MODEL_ID, history=user_sessions[chat_id]["history"])
    # 첫 출력은 인사말/스몰토크 없이 '문제만' 중국어로 제시해야 TTS가 정상 동작한다.
    response = chat.send_message(
        "스몰토크나 인사말 없이, 첫 번째 문제를 중국어로만 제공해. "
        "한국어(라벨/설명/번호 포함)와 불필요한 메타 문구는 절대 넣지 말고 '문제 문장/문항 내용'만 보내줘."
    )
    
    text_response = response.text
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    # 질문(중국어)에는 TTS를 보내지만, 한국어가 섞여 있으면(문제해석/첨삭) 음성은 생략한다.
    if not contains_hangul(text_response):
        await send_voice_message(context, chat_id, text_response)
    
    user_sessions[chat_id]["history"].append(types.Content(role="model", parts=[types.Part(text=text_response)]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_requested
    chat_id = update.effective_chat.id
    user_text = update.message.text

    is_translation_request = ("문제설명" in user_text) or ("문제해석" in user_text)
    
    # [추가] 사용자가 직접 '수업종료' 입력 시 처리
    if "수업종료" in user_text.replace(" ", ""):
        logger.info(f"사용자 요청으로 수업 종료 (Chat ID: {chat_id})")
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
        # 한국어(문제 해석/답변 첨삭)는 음성 송출을 생략해도 된다고 했으므로,
        # 한글이 포함된 응답은 TTS를 건너뛴다.
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
    # GitHub Actions에서는 항상 즉시 실행하도록 처리
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
        
        # 종료 요청 시 폴링/종료를 정리한다.
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
