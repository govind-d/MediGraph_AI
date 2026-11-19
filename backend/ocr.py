"""Prescription image intake (OCR).

Engines are tried in order of expected accuracy for prescriptions and the first
available one is used. Every engine is optional; the module always reports what
it can and cannot do rather than failing.

  1. vision-llm  - a multimodal model transcribes the image. Best for
                   handwriting. Requires LLM_PROVIDER + an API key, and the
                   image is sent to that provider, so it is opt-in per upload.
  2. tesseract   - local, offline, free. Good on printed and clearly written
                   text, weak on cursive handwriting.
  3. easyocr     - local deep-learning OCR, used if installed.
  4. pdf-text    - for digital (non-scanned) PDFs, the embedded text layer is
                   read directly, which is exact rather than recognised.

Safety rule for this whole module: OCR output is a *draft transcription*, never
a prescription. It is always shown to the user for correction before any
parsing or safety checking, it is never auto-analysed, and the resulting
prescription is stored with `source='ocr'` so the record shows it was machine
-read. Low-confidence words are reported so the user knows where to look.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from utils.config import Config
from utils.security import log_error, log_info

MAX_IMAGE_BYTES = 12 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif")
SUPPORTED_TYPES = SUPPORTED_IMAGE_TYPES + ("pdf",)

_TESSERACT_HINTS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]


# --------------------------------------------------------------------------
# Engine discovery
# --------------------------------------------------------------------------
def _pillow():
    try:
        from PIL import Image, ImageFilter, ImageOps  # type: ignore

        return Image, ImageOps, ImageFilter
    except Exception:
        return None, None, None


def tesseract_binary():
    """Locate the Tesseract executable (PATH, TESSERACT_CMD, or a known folder)."""
    configured = os.getenv("TESSERACT_CMD")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _TESSERACT_HINTS:
        if Path(candidate).exists():
            return candidate
    return None


def available_engines() -> list[dict]:
    """Describe every engine and whether it can run right now."""
    engines = []

    vision_ready = Config.llm_enabled() and Config.LLM_PROVIDER in ("anthropic", "openai")
    engines.append({
        "id": "vision-llm",
        "name": "Vision LLM (%s)" % (Config.LLM_PROVIDER if vision_ready else "not configured"),
        "available": vision_ready,
        "offline": False,
        "handwriting": "best available",
        "reason": "" if vision_ready else
                  "Set LLM_PROVIDER and an API key in .env to enable handwriting transcription.",
    })

    binary = tesseract_binary()
    try:
        import pytesseract  # type: ignore  # noqa: F401

        has_binding = True
    except Exception:
        has_binding = False
    engines.append({
        "id": "tesseract",
        "name": "Tesseract OCR (local)",
        "available": bool(binary and has_binding),
        "offline": True,
        "handwriting": "printed text only",
        "reason": "" if (binary and has_binding) else
                  ("Install the Python binding: python -m pip install pytesseract"
                   if binary else
                   "Install Tesseract (https://github.com/UB-Mannheim/tesseract/wiki) and, if it "
                   "is not on PATH, set TESSERACT_CMD in .env."),
        "path": binary,
    })

    try:
        import easyocr  # type: ignore  # noqa: F401

        easy = True
    except Exception:
        easy = False
    engines.append({
        "id": "easyocr",
        "name": "EasyOCR (local deep learning)",
        "available": easy,
        "offline": True,
        "handwriting": "limited",
        "reason": "" if easy else "Optional: python -m pip install easyocr (large download).",
    })

    try:
        import pypdf  # type: ignore  # noqa: F401

        pdf = True
    except Exception:
        pdf = False
    engines.append({
        "id": "pdf-text",
        "name": "PDF text layer (exact, not OCR)",
        "available": pdf,
        "offline": True,
        "handwriting": "n/a - digital PDFs only",
        "reason": "" if pdf else "Optional: python -m pip install pypdf",
    })
    return engines


def engine_status() -> dict:
    engines = available_engines()
    return {
        "engines": engines,
        "any_available": any(e["available"] for e in engines),
        "default": next((e["id"] for e in engines if e["available"] and e["id"] != "pdf-text"), None),
    }


# --------------------------------------------------------------------------
# Preprocessing
# --------------------------------------------------------------------------
def preprocess(image_bytes: bytes, *, upscale: bool = True, binarize: bool = True,
               deskew_hint: bool = True):
    """Clean an image for OCR. Returns (processed_bytes, notes). Never raises."""
    Image, ImageOps, ImageFilter = _pillow()
    notes = []
    if Image is None:
        return image_bytes, ["Pillow is not installed, so the image was sent to OCR unprocessed."]
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        img = ImageOps.grayscale(img)
        notes.append("converted to greyscale")

        if upscale and max(img.size) < 1800:
            factor = min(3.0, 1800 / max(img.size))
            img = img.resize((int(img.width * factor), int(img.height * factor)),
                             Image.LANCZOS)
            notes.append("upscaled %.1fx for small text" % factor)

        img = ImageOps.autocontrast(img, cutoff=1)
        notes.append("auto-contrast")
        img = img.filter(ImageFilter.MedianFilter(size=3))
        notes.append("median denoise")

        if binarize:
            # Simple global threshold from the mean, which suits flat-lit scans.
            histogram = img.histogram()
            total = sum(histogram) or 1
            mean = sum(i * n for i, n in enumerate(histogram)) / total
            threshold = max(90, min(200, int(mean * 0.92)))
            img = img.point(lambda p, t=threshold: 255 if p > t else 0)
            notes.append("binarised at threshold %d" % threshold)

        if deskew_hint and img.width < img.height and img.height / img.width > 2.2:
            notes.append("image is very tall - crop to the medication list for better results")

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue(), notes
    except Exception as exc:
        log_error("image preprocessing", exc)
        return image_bytes, ["preprocessing failed (%s); the original image was used"
                             % type(exc).__name__]


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------
def _run_tesseract(image_bytes: bytes):
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore

    binary = tesseract_binary()
    if binary:
        pytesseract.pytesseract.tesseract_cmd = binary
    img = Image.open(io.BytesIO(image_bytes))
    # PSM 6: assume a uniform block of text, which suits a medication list.
    config = "--oem 3 --psm 6"
    text = pytesseract.image_to_string(img, config=config)
    data = pytesseract.image_to_data(img, config=config,
                                     output_type=pytesseract.Output.DICT)
    words, confidences, low = [], [], []
    for word, conf in zip(data.get("text", []), data.get("conf", [])):
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        if not str(word).strip() or conf < 0:
            continue
        words.append(word)
        confidences.append(conf)
        if conf < 70:
            low.append({"word": word, "confidence": round(conf, 1)})
    mean_conf = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    return {
        "text": text,
        "confidence": mean_conf,
        "low_confidence_words": low[:40],
        "word_count": len(words),
    }


def _run_easyocr(image_bytes: bytes):  # pragma: no cover - optional heavy dependency
    import easyocr  # type: ignore

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    results = reader.readtext(image_bytes, detail=1, paragraph=False)
    lines, confidences, low = [], [], []
    for _box, text, conf in results:
        lines.append(text)
        confidences.append(float(conf) * 100)
        if float(conf) < 0.7:
            low.append({"word": text, "confidence": round(float(conf) * 100, 1)})
    return {
        "text": "\n".join(lines),
        "confidence": round(sum(confidences) / len(confidences), 1) if confidences else 0.0,
        "low_confidence_words": low[:40],
        "word_count": len(lines),
    }


_VISION_PROMPT = (
    "Transcribe this prescription image into plain text, exactly as written. Rules:\n"
    "- Output one medication per line.\n"
    "- Copy names, strengths, doses, frequencies and durations verbatim, including "
    "abbreviations such as PO, BD, TDS, q8h, PRN.\n"
    "- Do NOT correct, expand, complete or guess anything. If a character is unreadable, "
    "write [?] in its place.\n"
    "- If a field is absent, leave it absent. Never invent a dose, strength or duration.\n"
    "- Output only the transcription, with no commentary.\n"
    "After the transcription, add a final line starting with 'UNCERTAIN:' listing any words "
    "you were unsure about, comma separated, or 'UNCERTAIN: none'."
)


def _run_vision_llm(image_bytes: bytes, media_type: str = "image/png"):
    """Transcribe with a multimodal model. Returns the engine result dict."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    text = None
    if Config.LLM_PROVIDER == "anthropic":
        body = json.dumps({
            "model": Config.ANTHROPIC_MODEL,
            "max_tokens": 1500,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                 "data": encoded}},
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json",
                     "x-api-key": Config.ANTHROPIC_API_KEY or "",
                     "anthropic-version": "2023-06-01"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
    elif Config.LLM_PROVIDER == "openai":
        body = json.dumps({
            "model": Config.OPENAI_MODEL,
            "max_tokens": 1500,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": "data:%s;base64,%s" % (media_type, encoded)}},
                ],
            }],
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=body,
            headers={"content-type": "application/json",
                     "authorization": "Bearer %s" % (Config.OPENAI_API_KEY or "")},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = (data["choices"][0]["message"]["content"] or "").strip()

    if not text:
        raise RuntimeError("the vision model returned no text")

    uncertain = []
    lines = text.splitlines()
    if lines and lines[-1].upper().startswith("UNCERTAIN:"):
        listed = lines[-1].split(":", 1)[1].strip()
        lines = lines[:-1]
        if listed and listed.lower() != "none":
            uncertain = [{"word": w.strip(), "confidence": None}
                         for w in listed.split(",") if w.strip()]
    body_text = "\n".join(lines).strip()
    unreadable = body_text.count("[?]")
    return {
        "text": body_text,
        "confidence": None,  # vision models do not return calibrated confidences
        "low_confidence_words": uncertain,
        "word_count": len(body_text.split()),
        "unreadable_markers": unreadable,
    }


def _run_pdf_text(file_bytes: bytes):
    import pypdf  # type: ignore

    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n".join(p for p in pages if p).strip()
    return {
        "text": text,
        "confidence": 100.0 if text else 0.0,
        "low_confidence_words": [],
        "word_count": len(text.split()),
        "pages": len(reader.pages),
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def extract_text(file_bytes: bytes, filename: str = "upload.png", *, engine: str = "auto",
                 preprocess_image: bool = True, allow_cloud: bool = False) -> dict:
    """Transcribe an uploaded prescription image or PDF.

    Returns a dict with: ok, text, engine, engine_label, confidence,
    low_confidence_words, notes, warnings, errors.

    `allow_cloud` must be True before the image is sent to a vision LLM, because
    that leaves the machine.
    """
    result = {
        "ok": False,
        "text": "",
        "engine": None,
        "engine_label": None,
        "confidence": None,
        "low_confidence_words": [],
        "notes": [],
        "warnings": [],
        "errors": [],
        "lines_detected": 0,
    }

    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix and suffix not in SUPPORTED_TYPES:
        result["errors"].append(
            "Unsupported file type '.%s'. Upload one of: %s."
            % (suffix, ", ".join(SUPPORTED_TYPES))
        )
        return result
    if not file_bytes:
        result["errors"].append("The uploaded file is empty.")
        return result
    if len(file_bytes) > MAX_IMAGE_BYTES:
        result["errors"].append(
            "The file is %.1f MB; the limit is %d MB. Photograph just the medication list."
            % (len(file_bytes) / 1e6, MAX_IMAGE_BYTES // (1024 * 1024))
        )
        return result

    is_pdf = suffix == "pdf" or file_bytes[:4] == b"%PDF"
    status = engine_status()
    by_id = {e["id"]: e for e in status["engines"]}

    if is_pdf:
        chosen = "pdf-text"
    elif engine != "auto":
        chosen = engine
    else:
        if by_id["vision-llm"]["available"] and allow_cloud:
            chosen = "vision-llm"
        elif by_id["tesseract"]["available"]:
            chosen = "tesseract"
        elif by_id["easyocr"]["available"]:
            chosen = "easyocr"
        else:
            chosen = None

    if chosen is None:
        result["errors"].append(
            "No OCR engine is available. Install Tesseract "
            "(https://github.com/UB-Mannheim/tesseract/wiki) and run "
            "`python -m pip install pytesseract`, or configure a vision LLM in .env. "
            "You can always type the prescription instead."
        )
        return result
    if chosen not in by_id:
        result["errors"].append("Unknown OCR engine '%s'." % chosen)
        return result
    if not by_id[chosen]["available"]:
        result["errors"].append("%s is not available. %s"
                                % (by_id[chosen]["name"], by_id[chosen]["reason"]))
        return result
    if chosen == "vision-llm" and not allow_cloud:
        result["errors"].append(
            "Sending the image to the vision model requires explicit consent, because the "
            "image leaves this machine."
        )
        return result

    payload = file_bytes
    if not is_pdf and preprocess_image and chosen != "vision-llm":
        payload, notes = preprocess(file_bytes)
        result["notes"].extend(notes)

    try:
        if chosen == "tesseract":
            engine_result = _run_tesseract(payload)
        elif chosen == "easyocr":
            engine_result = _run_easyocr(payload)
        elif chosen == "vision-llm":
            engine_result = _run_vision_llm(
                payload, "application/pdf" if is_pdf else _media_type(suffix)
            )
        else:
            engine_result = _run_pdf_text(payload)
    except Exception as exc:
        log_error("OCR engine %s" % chosen, exc)
        result["errors"].append(
            "%s could not read this file (%s). Try a sharper, straighter photograph, or type "
            "the prescription instead." % (by_id[chosen]["name"], type(exc).__name__)
        )
        return result

    text = clean_ocr_text(engine_result.get("text", ""))
    result.update({
        "ok": bool(text.strip()),
        "text": text,
        "engine": chosen,
        "engine_label": by_id[chosen]["name"],
        "confidence": engine_result.get("confidence"),
        "low_confidence_words": engine_result.get("low_confidence_words", []),
        "lines_detected": len([ln for ln in text.splitlines() if ln.strip()]),
    })

    if not result["ok"]:
        result["errors"].append(
            "No text could be read from this file. Make sure the medication list is in focus, "
            "well lit and straight, or type it instead."
        )
        return result

    result["warnings"].extend(_quality_warnings(result, engine_result, chosen))
    log_info("OCR completed with %s: %d line(s)" % (chosen, result["lines_detected"]))
    return result


def _media_type(suffix: str) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif"}.get(suffix, "image/png")


def _quality_warnings(result, engine_result, chosen) -> list[str]:
    warnings = [
        "This is a machine transcription, not a prescription. Check every character against "
        "the original before analysing it - OCR misreads handwriting, and a misread strength "
        "or frequency is dangerous."
    ]
    confidence = result.get("confidence")
    if confidence is not None and confidence < 75:
        warnings.append(
            "Average character confidence was %.0f%%, which is low. Expect errors, especially in "
            "numbers." % confidence
        )
    if result["low_confidence_words"]:
        warnings.append(
            "Uncertain words: %s. Check these first."
            % ", ".join(w["word"] for w in result["low_confidence_words"][:12])
        )
    if engine_result.get("unreadable_markers"):
        warnings.append(
            "%d unreadable character(s) were marked [?] and must be filled in by you."
            % engine_result["unreadable_markers"]
        )
    if chosen == "tesseract":
        warnings.append(
            "Tesseract reads printed text well but handwriting poorly. For a handwritten script, "
            "configure a vision LLM or type the text."
        )
    if chosen == "vision-llm":
        warnings.append("The image was sent to %s for transcription." % Config.LLM_PROVIDER)
    digits = len(re.findall(r"\d", result["text"]))
    if digits == 0:
        warnings.append(
            "No digits were recognised, so no strength, dose or duration was captured. That "
            "usually means the image is too blurred or too low-resolution."
        )
    return warnings


_LINE_NOISE_RE = re.compile(r"^[\W_]+$")
_OCR_FIXES = [
    # Conservative, unit-anchored fixes only: never touch a bare number.
    (re.compile(r"(\d)\s*(?:rng|mq|rnq|ng)\b", re.IGNORECASE), r"\1mg"),
    (re.compile(r"(\d)\s*m\s*g\b", re.IGNORECASE), r"\1mg"),
    (re.compile(r"(\d)\s*(?:mcq|rncg|ucg)\b", re.IGNORECASE), r"\1mcg"),
    (re.compile(r"\bt\.?d\.?s\.?\b", re.IGNORECASE), "TDS"),
    (re.compile(r"\bb\.?d\.?\b(?!\w)", re.IGNORECASE), "BD"),
    (re.compile(r"\bo\.?d\.?\b(?!\w)", re.IGNORECASE), "OD"),
    (re.compile(r"[ \t]{2,}"), " "),
]


def clean_ocr_text(text: str) -> str:
    """Tidy OCR output without changing any clinical content.

    Only whitespace, obvious unit mangling (`500rng` -> `500mg`) and empty
    noise lines are touched. Numbers themselves are never altered, because a
    "helpful" correction of a dose is exactly the failure mode to avoid.
    """
    if not text:
        return ""
    lines = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or _LINE_NOISE_RE.match(line):
            continue
        for pattern, replacement in _OCR_FIXES:
            line = pattern.sub(replacement, line)
        lines.append(line.strip())
    # Collapse runs of blank lines, keep at most 40 lines
    return "\n".join(lines[:40]).strip()


def describe_engines() -> str:
    """One-line summary for the Settings page."""
    parts = []
    for engine in available_engines():
        parts.append("%s: %s" % (engine["name"], "available" if engine["available"] else "unavailable"))
    return "; ".join(parts)
