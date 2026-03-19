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

# 로깅 설정 (콘솔 및 파일 출력)
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

# 1. 환경 설정
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
try:
    CHAT_ID_ENV = os.getenv("CHAT_ID")
    CHAT_ID = int(CHAT_ID_ENV) if CHAT_ID_ENV else 0
except (ValueError, TypeError):
    CHAT_ID = 0
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "12:00")
WRONG_NOTES_FILE = "wrong_notes.md"

# 시작 시 오답노트 파일 존재 보장
if not os.path.exists(WRONG_NOTES_FILE):
    with open(WRONG_NOTES_FILE, "w", encoding="utf-8") as f:
        f.write("# 🇨🇳 TSC 트레이닝 센터 오답 노트\n---\n")

if not TELEGRAM_TOKEN or not GEMINI_KEY or CHAT_ID == 0:
    logger.error(f"환경 변수 설정 오류: TOKEN={bool(TELEGRAM_TOKEN)}, KEY={bool(GEMINI_KEY)}, CHAT_ID={CHAT_ID}")
    exit(1)

# New SDK Client
client = genai.Client(api_key=GEMINI_KEY)
MODEL_ID = "gemini-2.0-flash" # tistory-reddit-agent에서 사용한 최신 안정 모델 (2.5는 오타 가능성 대비 2.0 권장하나 유저 코드 따라감)
# 유저가 2.5를 명시했으므로 2.5로 시도하나, 안될 경우 2.0으로 가이드 예정. 
# 일단 generator.py에 적힌 "gemini-2.5-flash"를 그대로 사용.
MODEL_ID = "gemini-2.5-flash"

user_sessions = {}

# 2. TTS 변환 및 발송 함수
async def send_voice_message(context, chat_id, text):
    """텍스트를 음성으로 변환하여 텔레그램으로 전송"""
    try:
        tts = gTTS(text=text, lang='zh-cn')
        voice_file = io.BytesIO()
        tts.write_to_fp(voice_file)
        voice_file.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=voice_file)
    except Exception as e:
        logger.error(f"TTS 에러: {e}")

# 3. 오답 관리
def get_past_mistakes():
    if os.path.exists(WRONG_NOTES_FILE):
        with open(WRONG_NOTES_FILE, "r", encoding="utf-8") as f:
            return f.read()[-1500:]
    return "기존 오답 기록 없음"

def append_to_wrong_notes(content):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(WRONG_NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n### {date_str} 복습\n{content}\n---\n")

# 4. 시스템 프롬프트
def get_system_prompt():
    past_mistakes = get_past_mistakes()
    return f"""
    너는 TSC 전문 중국어 선생님이야. Part 1은 생략하고 Part 2~7을 집중 훈련시켜.
    
    [핵심 규칙]
    1. 모든 질문은 반드시 중국어로만 먼저 제시해.
    2. 사용자가 이해 못 할 때만 한국어 번역을 제공해.
    3. 사용자의 대답 후에는 상세한 피드백(교정/Pinyin)을 줘.
    4. 과거 오답({past_mistakes})을 활용해.
    5. 10문제 완료 시 "수업 종료"라고 말해.
    """

async def start_lesson(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.effective_chat.id if hasattr(context, "effective_chat") and context.effective_chat else CHAT_ID
    
    logger.info(f"수업 시작 (Chat ID: {chat_id})")
    prompt = get_system_prompt()
    
    # New SDK History Format
    user_sessions[chat_id] = {
        "history": [types.Content(role="user", parts=[types.Part(text=prompt)])]
    }
    
    # Create Chat Session
    chat = client.chats.create(model=MODEL_ID, history=user_sessions[chat_id]["history"])
    response = chat.send_message("수업을 시작하자. 스몰토크 후 첫 번째 문제를 중국어로만 내줘.")
    
    text_response = response.text
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    await send_voice_message(context, chat_id, text_response)
    
    user_sessions[chat_id]["history"].append(types.Content(role="model", parts=[types.Part(text=text_response)]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_sessions: return

    session = user_sessions[chat_id]
    
    # Create Chat with History
    chat = client.chats.create(model=MODEL_ID, history=session["history"])
    response = chat.send_message(update.message.text)
    
    full_text = response.text
    display_text = full_text.split("---WRONG---")[0] if "---WRONG---" in full_text else full_text
    
    if "---WRONG---" in full_text:
        append_to_wrong_notes(full_text.split("---WRONG---")[1].strip())

    await update.message.reply_text(display_text)
    
    if "수업 종료" not in full_text:
        await send_voice_message(context, chat_id, display_text)
        session["history"].append(types.Content(role="user", parts=[types.Part(text=update.message.text)]))
        session["history"].append(types.Content(role="model", parts=[types.Part(text=full_text)]))
    else:
        logger.info("수업이 종료되었습니다. 봇을 정지합니다.")
        application = context.application
        await application.stop()
        await application.shutdown()
        os._exit(0) 

async def wait_until_scheduled_time():
    """설정된 시간까지 대기합니다."""
    now = datetime.now()
    hour, minute = map(int, SCHEDULE_TIME.split(":"))
    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    if target_time < now:
        from datetime import timedelta
        target_time += timedelta(days=1)
        
    wait_seconds = (target_time - now).total_seconds()
    logger.info(f"예약된 시간({SCHEDULE_TIME})까지 대기 중... ({int(wait_seconds)}초 남음)")
    await asyncio.sleep(wait_seconds)

async def main():
    import sys
    
    if "--now" in sys.argv:
        logger.info("--now 인자가 감지되었습니다. 즉시 수업을 시작합니다.")
    else:
        await wait_until_scheduled_time()
    
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
        
        logger.info(f"첫 메시지 전송 중 (Chat ID: {CHAT_ID})...")
        await start_lesson(MockContext(application))
        
        logger.info("봇이 활성화되었습니다. 응답을 기다립니다.")
        await application.updater.start_polling()
        
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"봇 실행 중 에러 발생: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("봇이 사용자에 의해 종료되었습니다.")
        pass
