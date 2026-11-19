"""Password hashing, input validation and privacy-safe logging."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from utils.config import BASE_DIR, Config

# --------------------------------------------------------------------------
# Logging: never write patient identifiers into the log file.
# --------------------------------------------------------------------------
_LOG_PATH = BASE_DIR / "medigraph.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler(_LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("medigraph")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_LONG_NUM_RE = re.compile(r"\b\d{6,}\b")


def scrub(text) -> str:
    """Remove obvious personal identifiers before logging."""
    out = _EMAIL_RE.sub("<email>", str(text))
    return _LONG_NUM_RE.sub("<number>", out)


def log_error(where: str, exc: Exception) -> None:
    logger.error("%s failed: %s: %s", where, type(exc).__name__, scrub(exc))


def log_info(message: str) -> None:
    logger.info("%s", scrub(message))


# --------------------------------------------------------------------------
# Passwords (PBKDF2-HMAC-SHA256, per-user random salt)
# --------------------------------------------------------------------------
def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), Config.PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    try:
        candidate, _ = hash_password(password, salt)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, password_hash)


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def validate_username(username: str):
    if not username or not _USERNAME_RE.match(username.strip()):
        return False, "Username must be 3-32 characters (letters, digits, . _ -)."
    return True, ""


def validate_password(password: str):
    if len(password or "") < 8:
        return False, "Password must be at least 8 characters."
    if password.isdigit() or password.isalpha():
        return False, "Use a mix of letters and numbers."
    return True, ""


def validate_email(email: str):
    if not email:
        return True, ""  # optional field
    if not _EMAIL_RE.fullmatch(email.strip()):
        return False, "Enter a valid email address."
    return True, ""


def sanitize_text(text, max_len: int = 5000) -> str:
    """Strip control characters and cap the length of free-text input."""
    if text is None:
        return ""
    cleaned = "".join(c for c in str(text) if c in "\n\t" or ord(c) >= 32)
    return cleaned.strip()[:max_len]


def new_token() -> str:
    return secrets.token_urlsafe(32)
