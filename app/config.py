"""Settings and API key loading.

The .env file is looked up first in this repo's folder, then in the folder
above it (where the teaching staff keep their keys). For local development we
keep walking up a couple more levels so a shared course .env is also found.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_DIR / "data"
DB_PATH = Path(os.environ.get("APPRAISAL_DB", DATA_DIR / "appraisal_commons.db"))

MODEL = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"

# Hard spending guard rails (see app/meter.py).
MAX_OUTPUT_TOKENS_PER_CALL = 1200
# The answering agents only restate tool results, so low reasoning keeps them from spending
# the whole output cap thinking (which left empty answers). Extractor/Verifier keep the default.
REASONING_EFFORT = {"concierge": "low", "guide": "low"}
MAX_TOOL_ROUNDS = 6
SESSION_BUDGET_USD = float(os.environ.get("SESSION_BUDGET_USD", "0.25"))
DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "2.00"))


def _load_env() -> Path | None:
    candidates = [REPO_DIR / ".env", REPO_DIR.parent / ".env"]  # the grading layout
    # Local development only: a shared course .env a couple of levels up. Only PORTKEY_API_KEY
    # decides which file "wins"; set the optional settings in one of the first two files.
    candidates += [p / ".env" for p in REPO_DIR.parents[1:3]]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            if os.environ.get("PORTKEY_API_KEY"):
                return path
    return None


ENV_FILE = _load_env()


def api_key() -> str | None:
    return os.environ.get("PORTKEY_API_KEY") or None
