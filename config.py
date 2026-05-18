import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_URL = os.getenv("DATABASE_URL")
SITE_URL = os.getenv("SITE_URL", "https://www.taklifnomachi.online")
SITE_API_KEY = os.getenv("SITE_API_KEY", "")
PAYMENT_GROUP_ID = int(os.getenv("PAYMENT_GROUP_ID", "0")) if os.getenv("PAYMENT_GROUP_ID") else None
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
PORT = int(os.getenv("PORT", "10000"))
