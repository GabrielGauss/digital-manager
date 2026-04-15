from dotenv import load_dotenv
import os

load_dotenv()

# MercadoLibre
ML_APP_ID = os.getenv("ML_APP_ID")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET")
ML_REDIRECT_URI = os.getenv("ML_REDIRECT_URI", "https://httpbin.org/get")
ML_SITE_ID = os.getenv("ML_SITE_ID", "MLA")  # MLA = Argentina

# Google Drive
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "google_token.json")
DRIVE_ROOT_FOLDER_ID = os.getenv("DRIVE_ROOT_FOLDER_ID", "1gJDz9ehwu2p3pm64eNg7hjGKXKc439NV")

# Email (Resend)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")

# Database
# Railway sets DATABASE_URL as postgres:// — SQLAlchemy needs postgresql+asyncpg://
_db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./packmanager.db")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
DATABASE_URL = _db_url

# App
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")

# Sync interval in hours (default 6)
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))
