import os
from dotenv import load_dotenv

_project_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_project_dir, ".env"))

DISCOUNT_RATE = float(os.getenv("DISCOUNT_RATE", "0.10"))
TERMINAL_GROWTH_RATE = float(os.getenv("TERMINAL_GROWTH_RATE", "0.03"))
PROJECTION_YEARS = int(os.getenv("PROJECTION_YEARS", "10"))
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "aganai aganai@example.com")

DB_PATH = os.getenv("DB_PATH", os.path.join(_project_dir, "aganai.db"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
SEC_RATE_LIMIT = float(os.getenv("SEC_RATE_LIMIT", "0.1"))
PROGRESS_LOG_INTERVAL = int(os.getenv("PROGRESS_LOG_INTERVAL", "50"))
