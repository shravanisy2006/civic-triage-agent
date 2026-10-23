"""Central configuration. All secrets come from environment variables (.env), never from code."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def _secret(name: str, default: str = "") -> str:
    """Read a setting from the environment (.env locally), or from Streamlit secrets when deployed
    on Streamlit Community Cloud. Secrets are never written to code or logs."""
    value = os.getenv(name)
    if value is None:
        try:
            import streamlit as st
            value = st.secrets.get(name)
        except Exception:  # no secrets file configured - that's fine locally
            value = None
    return str(value if value is not None else default).strip()


GEMINI_API_KEY = _secret("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "20000"))

OFFICER_PIN = _secret("OFFICER_PIN", "1234")
USING_DEFAULT_PIN = OFFICER_PIN == "1234"

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "civic.db")))

# Security limits
MAX_IMAGE_BYTES = 5 * 1024 * 1024        # 5 MB
MAX_IMAGE_PIXELS = 4000 * 4000           # blocks "decompression bomb" images
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_TEXT_CHARS = 500
MAX_AREA_CHARS = 60
RATE_LIMIT_COUNT = 5                     # submissions...
RATE_LIMIT_WINDOW_S = 60                 # ...per this many seconds, per session

# Efficiency: images are downscaled before being sent to Gemini (fewer tokens, faster)
AI_IMAGE_MAX_SIDE = 1024

DUPLICATE_WINDOW_DAYS = 7
