import os

from dotenv import load_dotenv

load_dotenv()

DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "4.0"))
GEMINI_CONTEXT_LINES = int(os.getenv("GEMINI_CONTEXT_LINES", "8"))
GEMINI_CONFIDENCE_THRESHOLD = float(os.getenv("GEMINI_CONFIDENCE_THRESHOLD", "0.67"))
GEMINI_TERM_COOLDOWN_SECONDS = int(os.getenv("GEMINI_TERM_COOLDOWN_SECONDS", "45"))
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
GEMINI_RETRY_BASE_SECONDS = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "0.2"))
