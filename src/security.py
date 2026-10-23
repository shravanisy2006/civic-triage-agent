"""Input security: image validation + metadata stripping, PII redaction,
prompt-injection detection, length limits and rate limiting."""
import hashlib
import hmac
import io
import re
import time

from PIL import Image, UnidentifiedImageError

from src import config


class ValidationError(ValueError):
    """Raised when user input is rejected. The message is safe to show to the user."""


# ---------- Images ----------
# File signatures ("magic numbers"). Checked before any image decoder touches the bytes.
_SIGNATURES = {
    "JPEG": (b"\xff\xd8\xff",),
    "PNG": (b"\x89PNG\r\n\x1a\n",),
    "WEBP": (b"RIFF",),  # plus b"WEBP" at offset 8, checked below
}


def sniff_image_type(data: bytes) -> str | None:
    """Return 'JPEG', 'PNG' or 'WEBP' from the file header, or None if it is not one of them."""
    if data.startswith(_SIGNATURES["JPEG"]):
        return "JPEG"
    if data.startswith(_SIGNATURES["PNG"]):
        return "PNG"
    if data.startswith(_SIGNATURES["WEBP"]) and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def validate_and_clean_image(data: bytes) -> tuple[bytes, str]:
    """Check size, real file type (not just the extension) and dimensions,
    then re-encode the image. Re-encoding drops ALL metadata, including GPS (EXIF).
    Returns (clean_jpeg_bytes, sha256_of_clean_bytes)."""
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > config.MAX_IMAGE_BYTES:
        raise ValidationError("Image is larger than 5 MB.")
    declared = sniff_image_type(data)
    if declared is None:
        raise ValidationError("Only JPG, PNG or WEBP images are allowed.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()  # detects truncated / corrupted files
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValidationError("File is not a valid image.")
    if fmt not in config.ALLOWED_FORMATS or fmt != declared:
        raise ValidationError("Only JPG, PNG or WEBP images are allowed.")

    img = Image.open(io.BytesIO(data))
    if img.width * img.height > config.MAX_IMAGE_PIXELS:
        raise ValidationError("Image dimensions are too large.")
    img = img.convert("RGB")
    img.thumbnail((config.AI_IMAGE_MAX_SIDE, config.AI_IMAGE_MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)  # new file, no EXIF copied
    clean = out.getvalue()
    return clean, hashlib.sha256(clean).hexdigest()


# ---------- Text ----------
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\s-]?){10,13}")
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")

_INJECTION_PATTERNS = re.compile(
    r"(ignore (all|any|the|previous|above)|disregard (the|all|previous)|system prompt|"
    r"you are now|act as|new instructions|set severity|severity\s*[:=]\s*5|respond with|"
    r"</?citizen_report>)",
    re.IGNORECASE,
)


def redact_pii(text: str) -> str:
    """Remove contact details and ID numbers before text is stored or sent to the AI."""
    text = _EMAIL.sub("[email removed]", text)
    text = _AADHAAR.sub("[id removed]", text)
    text = _PHONE.sub("[phone removed]", text)
    return text


def clean_text(text: str, max_chars: int) -> str:
    text = (text or "").replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)  # control characters
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        raise ValidationError(f"Text must be at most {max_chars} characters.")
    return text


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_PATTERNS.search(text or ""))


def normalise_area(area: str) -> str:
    area = clean_text(area, config.MAX_AREA_CHARS)
    if not area:
        raise ValidationError("Please enter the area / locality.")
    if not re.fullmatch(r"[\w\s,.\-/#()]+", area):
        raise ValidationError("Area contains unsupported characters.")
    return area


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def md_escape(text: str | None) -> str:
    """Escape Markdown so user- or AI-supplied text is shown literally (no links, images or
    formatting) when passed to Streamlit elements that render Markdown."""
    return _MD_SPECIAL.sub(r"\\\1", text or "")


# ---------- Officer login ----------
class LoginGuard:
    """Locks officer login for a period after repeated wrong PINs (per session)."""

    def __init__(self, max_failures: int = 5, lockout_s: int = 300):
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self.failures = 0
        self.locked_until = 0.0

    def seconds_locked(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return max(0, int(self.locked_until - now))

    def check(self, pin: str, expected: str, now: float | None = None) -> bool:
        """Constant-time PIN comparison; returns False (without comparing) while locked out."""
        now = time.time() if now is None else now
        if self.seconds_locked(now):
            return False
        if pin and hmac.compare_digest(pin.encode(), expected.encode()):
            self.failures = 0
            return True
        self.failures += 1
        if self.failures >= self.max_failures:
            self.locked_until = now + self.lockout_s
            self.failures = 0
        return False


# ---------- Rate limiting ----------
class RateLimiter:
    """Sliding-window limiter. One instance per user session."""

    def __init__(self, limit: int = config.RATE_LIMIT_COUNT, window_s: int = config.RATE_LIMIT_WINDOW_S):
        self.limit = limit
        self.window_s = window_s
        self.hits: list[float] = []

    def allow(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self.hits = [t for t in self.hits if now - t < self.window_s]
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True
