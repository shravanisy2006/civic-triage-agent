"""Input security: image validation + metadata stripping, PII redaction,
prompt-injection detection, length limits and rate limiting."""
import hashlib
import io
import re
import time

from PIL import Image, UnidentifiedImageError

from src import config


class ValidationError(ValueError):
    """Raised when user input is rejected. The message is safe to show to the user."""


# ---------- Images ----------
def validate_and_clean_image(data: bytes) -> tuple[bytes, str]:
    """Check size, real file type (not just the extension) and dimensions,
    then re-encode the image. Re-encoding drops ALL metadata, including GPS (EXIF).
    Returns (clean_jpeg_bytes, sha256_of_clean_bytes)."""
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > config.MAX_IMAGE_BYTES:
        raise ValidationError("Image is larger than 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()  # detects truncated / corrupted files
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValidationError("File is not a valid image.")
    if fmt not in config.ALLOWED_FORMATS:
        raise ValidationError(f"Only JPG, PNG or WEBP images are allowed (got {fmt}).")

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
