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

# 2. TTS 변환 및 발송 함수
async def send_voice_message(context, chat_id, text):
    try:
        tts = gTTS(text=text, lang='zh-cn')
        voice_file = io.BytesIO()
        tts.write_to_fp(voice_file)
        voice_file.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=voice_file)
    except Exception as e:
        logger.error(f"TTS 에러: {e}")

# 3. 시스템 프롬프트 (오답노트 부분 제거)
def get_system_prompt():
    return """
    너는 TSC 전문 중국어 선생님이야. Part 1은 생략하고 Part 2~7을 집중 훈련시켜.
    
    [핵심 규칙]
    1. 모든 질문은 반드시 중국어로만 먼저 제시해.
    2. 사용자가 이해 못 할 때만 한국어 번역을 제공해.
    3. 사용자의 대답 후에는 상세한 피드백(교정/Pinyin)을 줘.
    4. 10문제 완료 시 "수업 종료"라고 말해.
    """

async def start_lesson(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.effective_chat.id if hasattr(context, "effective_chat") and context.effective_chat else CHAT_ID
    
    logger.info(f"수업 시작 (Chat ID: {chat_id})")
    prompt = get_system_prompt()
    
    user_sessions[chat_id] = {
        "history": [types.Content(role="user", parts=[types.Part(text=prompt)])]
    }
    
    chat = client.chats.create(model=MODEL_ID, history=user_sessions[chat_id]["history"])
    response = chat.send_message("수업을 시작하자. 스몰토크 후 첫 번째 문제를 중국어로만 내줘.")
    
    text_response = response.text
    await context.bot.send_message(chat_id=chat_id, text=text_response)
    await send_voice_message(context, chat_id, text_response)
    
    user_sessions[chat_id]["history"].append(types.Content(role="model", parts=[types.Part(text=text_response)]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    
    # [추가] 사용자가 직접 '수업종료' 입력 시 처리
    if "수업종료" in user_text.replace(" ", ""):
        logger.info(f"사용자 요청으로 수업 종료 (Chat ID: {chat_id})")
        await update.message.reply_text("수업을 종료합니다. 수고하셨습니다!")
        os._exit(0)

    if chat_id not in user_sessions: return

    session = user_sessions[chat_id]
    chat = client.chats.create(model=MODEL_ID, history=session["history"])
    response = chat.send_message(update.message.text)
    
    full_text = response.text
    await update.message.reply_text(full_text)
    
    if "수업 종료" not in full_text:
        await send_voice_message(context, chat_id, full_text)
        session["history"].append(types.Content(role="user", parts=[types.Part(text=update.message.text)]))
        session["history"].append(types.Content(role="model", parts=[types.Part(text=full_text)]))
    else:
        logger.info("수업이 종료되었습니다. 봇을 정지합니다.")
        os._exit(0) 

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
        
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"봇 실행 중 에러 발생: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("봇 종료")
