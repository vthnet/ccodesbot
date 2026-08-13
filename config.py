import os
from dotenv import load_dotenv

load_dotenv()


def _getenv(
    name: str,
    default: str | None = None,
    required: bool = False
) -> str:
    val = os.getenv(name, default)

    if required and (val is None or val == ""):
        raise RuntimeError(f"Missing required env var: {name}")

    return val


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = _getenv("BOT_TOKEN", required=True)

API_ID = int(_getenv("API_ID", "21377358"))

API_HASH = _getenv(
    "API_HASH",
    "e05bc1f4f03839db7864a99dbf72d1cd"
)


# ============================================================
# ADMIN
# ============================================================

ADMIN_IDS = [
    int(i.strip())
    for i in _getenv("ADMIN_IDS", "", required=True)
    .split(",")
    if i.strip()
]


# ============================================================
# FORCE JOIN
# ============================================================

MUST_JOIN_CHANNEL = "-1003411895884"


# ============================================================
# DATABASE
# ============================================================

DATABASE_URL = _getenv("DATABASE_URL", required=True)


# ============================================================
# SERVER 2 — TG-LION / TGPVA
# ============================================================

TGLION_API_KEY = _getenv("TGLION_API_KEY", "")
TGLION_ID = _getenv("TGLION_ID", "")
TGPVA_API_KEY = _getenv("TGPVA_API_KEY", "")


# ============================================================
# SERVER 3 — LZT
# ============================================================

LZT_API_KEY = _getenv("LZT_API_KEY", "")


# ============================================================
# SMM PANEL
# ============================================================

SMM_API_URL = _getenv(
    "SMM_API_URL",
    "https://cheapestsmmpanel.com/api/v2"
)

SMM_API_KEY = _getenv("SMM_API_KEY", "")


# ============================================================
# BOT SETTINGS
# ============================================================

DEFAULT_CURRENCY = _getenv("DEFAULT_CURRENCY", "₹")

MIN_BALANCE_REQUIRED = float(
    _getenv("MIN_BALANCE_REQUIRED", "0")
)