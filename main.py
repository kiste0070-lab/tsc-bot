import os
import io
import logging
from datetime import datetime
from dotenv import load_dotenv
import google.generativeai as genai
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

# [추가] 시작 시 오답노트 파일 존재 보장
if not os.path.exists(WRONG_NOTES_FILE):
    with open(WRONG_NOTES_FILE, "w", encoding="utf-8") as f:
        f.write("# 🇨🇳 TSC 트레이닝 센터 오답 노트\n---\n")

if not TELEGRAM_TOKEN or not GEMINI_KEY or CHAT_ID == 0:
    logger.error(f"환경 변수 설정 오류: TOKEN={bool(TELEGRAM_TOKEN)}, KEY={bool(GEMINI_KEY)}, CHAT_ID={CHAT_ID}")
    exit(1)

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
user_sessions = {}

# 이하 기존 함수들 (send_voice_message, get_past_mistakes, append_to_wrong_notes, get_system_prompt, start_lesson, handle_message, wait_until_scheduled_time, main) 동일...
