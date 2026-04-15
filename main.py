import asyncio
import logging
import os
import json
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database.db import init_db
from api.routes import auth, bundles, webhooks, orders, admin
from services.scheduler import scheduler_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _write_google_credentials():
    """
    In production (Railway), Google credential files are stored as env vars.
    Write them to disk at startup so the Drive service can read them normally.
    The token auto-refreshes during the session; on next restart the env var
    is written again (refresh_token stays valid permanently).
    """
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    token_json = os.getenv("GOOGLE_TOKEN_JSON")

    from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE

    if creds_json and not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        try:
            with open(GOOGLE_CREDENTIALS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("[startup] Wrote google_credentials.json from env var")
        except Exception as e:
            logger.error(f"[startup] Failed to write credentials: {e}")

    if token_json and not os.path.exists(GOOGLE_TOKEN_FILE):
        try:
            with open(GOOGLE_TOKEN_FILE, "w") as f:
                f.write(token_json)
            logger.info("[startup] Wrote google_token.json from env var")
        except Exception as e:
            logger.error(f"[startup] Failed to write token: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _write_google_credentials()
    await init_db()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Pack Digital Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(bundles.router)
app.include_router(webhooks.router)
app.include_router(orders.router)
app.include_router(admin.router)

# Serve the admin panel
try:
    app.mount("/static", StaticFiles(directory="site/static"), name="static")
except Exception:
    pass  # static folder may not exist


@app.get("/")
async def admin_panel():
    return FileResponse("site/index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
