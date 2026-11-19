"""Prescription NLP parser.

Strategy (deliberately conservative — a wrong dose is worse than a blank one):

1. Split the free text into one candidate line per medication.
2. Identify the medication name using a local RxNorm-derived vocabulary
   (generic names + brand names), falling back to a leading-token heuristic
   and, when installed, spaCy entity/POS hints.
3. Extract strength, dose, frequency, duration, route and special
   instructions with carefully bounded regular expressions.
4. Anything that cannot be found is reported in `needs_verification` instead
   of being guessed. Nothing is ever invented.

An optional LLM pass (`llm_structured_extract`) can be enabled to handle
unusual phrasing; it only ever *fills blanks* left by the rule-based pass.
"""
from __future__ import annotations

import json
import re

from utils.config import DATA_DIR, Config
from utils.security import log_error, sanitize_text

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------
_WORD_NUMBERS = {
    "half": 0.5, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fourteen": 14, "twenty": 20, "thirty": 30,
}

_FORM_WORDS = (
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps",
    "pill", "pills", "puff", "puffs", "drop", "drops", "spray", "sprays",
    "sachet", "sachets", "ml", "teaspoon", "teaspoonful", "tsp", "unit",
    "units", "patch", "patches", "suppository", "injection", "vial", "scoop",
)

_PREFIX_WORDS = (
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules",
    "inj", "injection", "syp", "syrup", "susp", "suspension", "oint",
    "ointment", "cream", "drops", "drop", "inhaler", "neb", "sol", "solution",
    "take", "give", "start", "continue", "rx", "medication", "med", "t",
)

_ROUTE_PATTERNS = (
    (r"\b(?:p\.?o\.?|by mouth|orally|per oral)\b", "oral"),
    (r"\b(?:sub ?lingual(?:ly)?|s\.?l\.?)\b", "sublingual"),
    (r"\b(?:inhal(?:e|ed|ation)|puffs?|nebuli[sz]|inhaler|mdi)\b", "inhalation"),
    (r"\b(?:topical(?:ly)?|apply .*skin|cutaneous)\b", "topical"),
    (r"\b(?:eye ?drops?|ophthalmic|in (?:each|both|the) eyes?)\b", "ophthalmic"),
    (r"\b(?:ear ?drops?|otic)\b", "otic"),
    (r"\b(?:nasal(?:ly)?|nostril|intranasal)\b", "nasal"),
    (r"\b(?:rectal(?:ly)?|p\.?r\.?|suppositor)\b", "rectal"),
    (r"\b(?:subcutaneous(?:ly)?|s\.?c\.?|sub-?cut)\b", "subcutaneous"),
    (r"\b(?:intramuscular(?:ly)?|i\.?m\.?)\b", "intramuscular"),
    (r"\b(?:intravenous(?:ly)?|i\.?v\.?)\b", "intravenous"),
    (r"\b(?:transdermal|patch(?:es)?)\b", "transdermal"),
    (r"\b(?:vaginal(?:ly)?|pessary)\b", "vaginal"),
)

# Forms that imply the oral route when no explicit route word is present
_ORAL_FORMS = ("tablet", "tab", "capsule", "cap", "pill", "syrup", "suspension",
               "teaspoon", "sachet", "liquid", "drops by mouth")

_STRENGTH_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mcg|µg|ug|mg|grams?|gm?|iu|units?|ml|l|%|mmol|meq)"
    r"(?:\s*/\s*(?P<per_value>\d+(?:\.\d+)?)?\s*(?P<per_unit>ml|l|mg|dose|day|tab(?:let)?|puff))?",
    re.IGNORECASE,
)

_DOSE_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?(?:\s*(?:-|to|or)\s*\d+(?:\.\d+)?)?|half|one|two|three|four|five|six|a|an)"
    r"\s*(?P<form>" + "|".join(_FORM_WORDS) + r")\b",
    re.IGNORECASE,
)

_DURATION_RE = re.compile(
    r"(?:\bfor\b|\bx\b|\bover\b|\bnext\b|×)\s*"
    r"(?P<qty>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fourteen|twenty|thirty)"
    r"\s*(?P<unit>day|days|week|weeks|month|months|year|years|dose|doses)\b",
    re.IGNORECASE,
)
_DURATION_ALT_RE = re.compile(
    r"(?P<qty>\d+)\s*(?P<unit>day|days|week|weeks|month|months)\s*(?:course|supply|therapy)\b",
    re.IGNORECASE,
)

_MAX_PER_DAY_RE = re.compile(
    r"(?:max(?:imum)?|up to|not more than|no more than)\s*(?:of\s*)?"
    r"(?P<qty>\d+|one|two|three|four|five|six)\s*"
    r"(?:doses?|tablets?|capsules?|puffs?|times?)?\s*"
    r"(?:(?:per|a|each|in a|in 24)\s*(?:day|24 hours?|24 hrs?))?",
    re.IGNORECASE,
)

# (regex, kind, canonical text, doses per day)
#   kind "hours" -> group(1) is an hourly interval
#   kind "times" -> group(1) is the number of doses per day
#   kind "fixed" -> label and doses-per-day are given literally
_FREQUENCY_PATTERNS = [
    (r"\bevery\s*(\d+)\s*(?:to\s*\d+\s*)?h(?:ours?|rs?|r)?\b", "hours", None, None),
    (r"\bq\s*(\d+)\s*h\b", "hours", None, None),
    (r"\b(\d+)\s*(?:times?|x)\s*(?:a|per|each|every)?\s*day\b", "times", None, None),
    (r"\b(\d+)\s*(?:times?|x)\s*daily\b", "times", None, None),
    (r"\bfour\s*times?\s*(?:a\s*)?(?:day|daily)\b", "fixed", "4 times daily", 4),
    (r"\bthree\s*times?\s*(?:a\s*)?(?:day|daily)\b", "fixed", "3 times daily", 3),
    (r"\btwice\s*(?:a\s*)?(?:day|daily)\b", "fixed", "2 times daily", 2),
    (r"\btwo\s*times?\s*(?:a\s*)?(?:day|daily)\b", "fixed", "2 times daily", 2),
    (r"\bonce\s*(?:a\s*)?(?:day|daily)\b", "fixed", "once daily", 1),
    (r"\bevery\s*other\s*day\b", "fixed", "every other day", 0.5),
    (r"\balternate\s*days?\b", "fixed", "every other day", 0.5),
    (r"\bonce\s*(?:a\s*)?week(?:ly)?\b", "fixed", "once weekly", 0.1428),
    (r"\bweekly\b", "fixed", "once weekly", 0.1428),
    (r"\bq\.?\s*i\.?\s*d\b|\bqds\b", "fixed", "4 times daily (QDS)", 4),
    (r"\bt\.?\s*i\.?\s*d\b|\btds\b", "fixed", "3 times daily (TDS)", 3),
    (r"\bb\.?\s*i\.?\s*d\b|\bbds?\b", "fixed", "2 times daily (BD)", 2),
    (r"\bo\.?\s*d\b|\bq\.?\s*d\b|\bomni die\b", "fixed", "once daily (OD)", 1),
    (r"\b(?:h\.?\s*s\.?|nocte|at night|before bed(?:time)?|at bed ?time)\b", "fixed",
     "once daily at night", 1),
    (r"\b(?:om|in the morning|each morning|every morning|mane)\b", "fixed",
     "once daily in the morning", 1),
    (r"\bdaily\b", "fixed", "once daily", 1),
]

_PRN_RE = re.compile(r"\b(?:p\.?r\.?n\.?|as (?:and when )?needed|as required|when required|if needed|sos)\b",
                     re.IGNORECASE)
_STAT_RE = re.compile(r"\b(?:stat|immediately|at once)\b", re.IGNORECASE)
_AS_DIRECTED_RE = re.compile(r"\bas (?:directed|advised|instructed|per doctor)\b", re.IGNORECASE)

_INSTRUCTION_PATTERNS = [
    (r"\bafter (?:food|meals?|eating)\b", "Take after food"),
    (r"\bwith (?:food|meals?|a meal)\b", "Take with food"),
    (r"\bbefore (?:food|meals?|eating)\b", "Take before food"),
    (r"\bempty stomach\b", "Take on an empty stomach"),
    (r"\bwith (?:a )?(?:full )?glass of water\b", "Take with a full glass of water"),
    (r"\bdo not (?:crush|chew|break)\b", "Do not crush or chew"),
    (r"\bavoid alcohol\b", "Avoid alcohol"),
    (r"\bavoid (?:milk|dairy|antacids?)\b", "Separate from milk, dairy or antacids"),
    (r"\bshake well\b", "Shake well before use"),
    (r"\bat night\b", "Take at night"),
    (r"\bin the morning\b", "Take in the morning"),
    (r"\bbefore bed(?:time)?\b", "Take before bedtime"),
    (r"\bfor (?:pain|fever|cough|nausea|sleep|anxiety|infection|blood pressure|sugar)\b", None),
    (r"\bsit (?:up)?right\b", "Stay upright after the dose"),
    (r"\btaper(?:ing)?\b", "Tapering schedule - confirm the exact steps with the prescriber"),
]

_SPLIT_LINE_RE = re.compile(r"[\n\r]+|\s*;\s*|\s+•\s*")
_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:\(?\d{1,2}[.)\]]|[-*•‣>]+|[a-h][.)])\s*")

_VOCAB_CACHE: dict | None = None


def _load_vocabulary() -> dict:
    """{lowercase alias -> {'normalized': ..., 'display': ..., 'record': {...}}}"""
    global _VOCAB_CACHE
    if _VOCAB_CACHE is not None:
        return _VOCAB_CACHE
    vocab: dict = {}
    path = DATA_DIR / "sample_medications.json"
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        log_error("loading medication vocabulary", exc)
        records = []
    for rec in records:
        norm = str(rec.get("normalized_name", "")).lower()
        aliases = {norm, str(rec.get("display_name", "")).lower()}
        aliases.update(a.lower() for a in rec.get("brand_names", []) or [])
        aliases.update(a.lower() for a in rec.get("ingredients", []) or [])
        # Display names such as "Acetaminophen (Paracetamol)" -> both halves
        display = str(rec.get("display_name", ""))
        if "(" in display:
            base, _, inner = display.partition("(")
            aliases.add(base.strip().lower())
            aliases.add(inner.replace(")", "").strip().lower())
        for alias in aliases:
            alias = alias.strip()
            if len(alias) >= 3:
                vocab.setdefault(alias, {"normalized": norm, "display": rec.get("display_name"), "record": rec})
    _VOCAB_CACHE = vocab
    return vocab


# --------------------------------------------------------------------------
# Optional spaCy support
# --------------------------------------------------------------------------
_NLP = None
_SPACY_STATE = "not loaded"


def get_spacy():
    """Load spaCy lazily. Returns None when unavailable (never raises)."""
    global _NLP, _SPACY_STATE
    if _NLP is not None or _SPACY_STATE.startswith("unavailable"):
        return _NLP
    if not Config.USE_SPACY:
        _SPACY_STATE = "unavailable (disabled via USE_SPACY)"
        return None
    try:
        import spacy  # type: ignore

        try:
            _NLP = spacy.load(Config.SPACY_MODEL, disable=["lemmatizer"])
            _SPACY_STATE = "loaded model %s" % Config.SPACY_MODEL
        except OSError:
            _NLP = spacy.blank("en")
            _SPACY_STATE = (
                "unavailable (model %s not downloaded - using blank tokenizer)" % Config.SPACY_MODEL
            )
    except Exception as exc:  # pragma: no cover - spaCy is optional
        _SPACY_STATE = "unavailable (%s)" % type(exc).__name__
        _NLP = None
    return _NLP


def spacy_status() -> str:
    get_spacy()
    return _SPACY_STATE


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _num(token) -> float | None:
    token = str(token).strip().lower().replace(",", ".")
    if token in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[token])
    m = re.match(r"^(\d+(?:\.\d+)?)", token)
    return float(m.group(1)) if m else None


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def split_medication_lines(text: str) -> list[str]:
    """Split raw prescription text into one candidate line per medication.

    Lines that carry only dosage instructions ("take one capsule three times
    daily") are joined onto the medication line above them, which is how
    printed and scanned prescriptions usually wrap.
    """
    vocab = _load_vocabulary()
    raw_lines = [ln.strip() for ln in _SPLIT_LINE_RE.split(text or "") if ln and ln.strip()]
    lines: list[str] = []
    for line in raw_lines:
        line = _NUMBER_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        pieces = _split_multi_drug_line(line, vocab)
        for piece in pieces:
            if lines and _is_continuation(piece, vocab):
                lines[-1] = "%s %s" % (lines[-1].rstrip(" ,;."), piece)
            else:
                lines.append(piece)
    return lines


# Words that belong to a dosage instruction rather than a medication name.
_SIG_WORDS = {
    "take", "takes", "taking", "give", "given", "apply", "use", "used", "inject", "insert",
    "swallow", "chew", "dissolve", "instil", "instill", "inhale", "puff", "puffs", "spray",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "half",
    "a", "an", "the", "and", "then", "with", "without", "before", "after", "food", "meal",
    "meals", "water", "milk", "morning", "night", "evening", "noon", "bedtime", "daily",
    "day", "days", "week", "weeks", "month", "months", "time", "times", "hour", "hours",
    "every", "once", "twice", "thrice", "for", "of", "as", "needed", "required", "directed",
    "advised", "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps",
    "pill", "pills", "drop", "drops", "sachet", "sachets", "ml", "unit", "units", "dose",
    "doses", "mg", "mcg", "gm", "course", "continue", "stop", "repeat", "po", "bd", "od",
    "tds", "qds", "bid", "tid", "qid", "hs", "prn", "stat", "sos", "nocte", "mane", "max",
    "maximum", "up", "to", "per", "at", "in", "on", "by", "mouth", "oral", "orally",
}


def _is_continuation(line: str, vocab: dict) -> bool:
    """True when a line carries only instructions for the medication above it."""
    normalized, _display, _record, _alias = _match_medication(line, vocab)
    if normalized:
        return False
    guess = _guess_medication_name(line)
    if not guess:
        # Nothing name-like at all (e.g. "1 tablet twice daily") - a continuation,
        # provided the line says something dosage-ish.
        return bool(re.search(r"\d|\b(?:%s)\b" % "|".join(sorted(_SIG_WORDS)), line, re.IGNORECASE))
    tokens = [t for t in re.split(r"[\s/.\-,]+", guess.lower()) if t]
    return bool(tokens) and all(t in _SIG_WORDS for t in tokens)


def _split_multi_drug_line(line: str, vocab: dict) -> list[str]:
    """Split a single line that mentions two or more known medications."""
    lowered = line.lower()
    hits: list[tuple[int, str]] = []
    for alias in vocab:
        start = 0
        while True:
            idx = lowered.find(alias, start)
            if idx == -1:
                break
            before_ok = idx == 0 or not lowered[idx - 1].isalnum()
            end = idx + len(alias)
            after_ok = end >= len(lowered) or not lowered[end].isalnum()
            if before_ok and after_ok:
                hits.append((idx, alias))
            start = idx + 1
    if len(hits) < 2:
        return [line]
    # Keep the longest alias starting at each position, then drop nested hits
    hits.sort(key=lambda h: (h[0], -len(h[1])))
    chosen: list[tuple[int, str]] = []
    for idx, alias in hits:
        if chosen and idx < chosen[-1][0] + len(chosen[-1][1]):
            continue
        chosen.append((idx, alias))
    if len(chosen) < 2:
        return [line]
    pieces = []
    for i, (idx, _alias) in enumerate(chosen):
        end = chosen[i + 1][0] if i + 1 < len(chosen) else len(line)
        piece = line[idx:end].strip(" ,.;-+&")
        # Re-attach a leading form word such as "Tab" from the original text
        if piece:
            pieces.append(piece)
    return pieces or [line]


def _match_medication(line: str, vocab: dict):
    """Return (normalized_name, display_name, record, matched_alias) or Nones."""
    lowered = line.lower()
    best = None
    for alias, info in vocab.items():
        idx = lowered.find(alias)
        if idx == -1:
            continue
        before_ok = idx == 0 or not lowered[idx - 1].isalnum()
        end = idx + len(alias)
        after_ok = end >= len(lowered) or not lowered[end].isalnum()
        if not (before_ok and after_ok):
            continue
        # Prefer the earliest match, then the longest alias
        key = (idx, -len(alias))
        if best is None or key < best[0]:
            best = (key, alias, info)
    if best is None:
        return None, None, None, None
    _key, alias, info = best
    return info["normalized"], info["display"], info["record"], alias


def _guess_medication_name(line: str) -> str | None:
    """Heuristic for a medication not present in the local vocabulary."""
    cleaned = _NUMBER_PREFIX_RE.sub("", line).strip()
    # Everything before the first number/strength is the best candidate
    head = re.split(r"\d", cleaned, maxsplit=1)[0]
    head = re.split(r"[,(]", head)[0]
    tokens = [t for t in re.split(r"[\s/.\-]+", head) if t]
    while tokens and tokens[0].lower().strip(".") in _PREFIX_WORDS:
        tokens.pop(0)
    tokens = [t for t in tokens if t.isalpha() and t.lower() not in _PREFIX_WORDS]
    if not tokens:
        return None
    # spaCy hint: prefer the token the model treats as a proper noun
    nlp = get_spacy()
    if nlp is not None and nlp.has_pipe("tagger"):
        try:
            doc = nlp(" ".join(tokens))
            propn = [t.text for t in doc if t.pos_ in ("PROPN", "NOUN")]
            if propn:
                tokens = propn
        except Exception:  # pragma: no cover - spaCy is best-effort only
            pass
    candidate = " ".join(tokens[:3]).strip()
    if len(candidate) < 3:
        return None
    return candidate


def _extract_strength(line: str):
    """First strength-looking quantity, skipping ones that belong to a duration."""
    for m in _STRENGTH_RE.finditer(line):
        before = line[max(0, m.start() - 12):m.start()].lower()
        if re.search(r"\b(?:for|x|next|over)\s*$", before):
            continue
        value = m.group("value").replace(",", ".")
        unit = m.group("unit").lower()
        unit = {"µg": "mcg", "ug": "mcg", "gm": "g", "grams": "g", "gram": "g", "unit": "units"}.get(unit, unit)
        text = "%s%s" % (value, "" if unit == "%" else unit)
        if m.group("per_unit"):
            per_val = m.group("per_value") or ""
            text += "/%s%s" % (per_val, m.group("per_unit").lower())
        return text
    return None


def _extract_dose(line: str, strength_text):
    m = _DOSE_RE.search(line)
    if m:
        qty_raw = m.group("qty").strip()
        form = m.group("form").lower()
        if form in ("ml", "unit", "units") and strength_text and qty_raw in strength_text:
            # The quantity doubles as the strength (e.g. "10 units", "5 ml syrup").
            return "%s %s" % (qty_raw, form)
        else:
            qty = _num(qty_raw.split("-")[0].split(" to ")[0])
            if qty is not None:
                form_label = form if qty != 1 else form.rstrip("s")
                if "-" in qty_raw or " to " in qty_raw or " or " in qty_raw:
                    return "%s %s" % (qty_raw.replace(" to ", "-").replace(" or ", "-"), form.rstrip("s") + "s")
                return "%s %s" % (_fmt_num(qty), form_label)
    # "take 5ml", "10 units" style doses
    m2 = re.search(r"\b(?:take|give|inject|administer)\s+(\d+(?:\.\d+)?)\s*(ml|units?|mg|mcg|puffs?|drops?)\b",
                   line, re.IGNORECASE)
    if m2:
        return "%s %s" % (m2.group(1), m2.group(2).lower())
    return None


def _extract_frequency(line: str):
    """Return (frequency_text, doses_per_day)."""
    lowered = line.lower()
    for pattern, kind, label, per_day in _FREQUENCY_PATTERNS:
        m = re.search(pattern, lowered, re.IGNORECASE)
        if not m:
            continue
        if kind == "hours":
            value = _num(m.group(1))
            if value is None or not 0 < value <= 24:
                continue
            return "every %s hours" % _fmt_num(value), round(24.0 / value, 2)
        if kind == "times":
            value = _num(m.group(1))
            if value is None or value <= 0:
                continue
            return "%s times daily" % _fmt_num(value), float(value)
        return label, float(per_day) if per_day else None
    return None, None


def _extract_duration(line: str):
    """Return (duration_text, duration_days)."""
    for regex in (_DURATION_RE, _DURATION_ALT_RE):
        m = regex.search(line)
        if not m:
            continue
        qty = _num(m.group("qty"))
        unit = m.group("unit").lower().rstrip("s")
        if qty is None:
            continue
        if unit == "dose":
            return "%s doses" % _fmt_num(qty), None
        factor = {"day": 1, "week": 7, "month": 30, "year": 365}.get(unit, 1)
        plural = "" if qty == 1 else "s"
        return "%s %s%s" % (_fmt_num(qty), unit, plural), int(qty * factor)
    return None, None


def _extract_route(line: str, dose_text):
    lowered = line.lower()
    for pattern, route in _ROUTE_PATTERNS:
        if re.search(pattern, lowered):
            return route, "explicit"
    haystack = "%s %s" % (lowered, (dose_text or "").lower())
    for form in _ORAL_FORMS:
        if form in haystack:
            return "oral", "inferred from dosage form"
    return None, None


def _extract_instructions(line: str):
    found = []
    for pattern, label in _INSTRUCTION_PATTERNS:
        m = re.search(pattern, line, re.IGNORECASE)
        if m and label:
            if label not in found:
                found.append(label)
        elif m and label is None:
            indication = m.group(0).strip()
            tidy = indication[0].upper() + indication[1:]
            if tidy not in found:
                found.append(tidy)
    if _PRN_RE.search(line):
        found.insert(0, "As needed (PRN)")
    if _STAT_RE.search(line):
        found.insert(0, "Immediately (STAT)")
    if _AS_DIRECTED_RE.search(line):
        found.insert(0, "As directed - exact instructions not written out")
    m = _MAX_PER_DAY_RE.search(line)
    if m:
        qty = _num(m.group("qty"))
        if qty:
            found.append("Maximum %s doses per day" % _fmt_num(qty))
    return "; ".join(found) if found else None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
VERIFIABLE_FIELDS = ("medication", "strength", "dose", "frequency", "duration", "route")


def parse_line(line: str) -> dict:
    """Parse one medication line into a structured record."""
    vocab = _load_vocabulary()
    line = sanitize_text(line, 600)
    normalized, display, record, alias = _match_medication(line, vocab)
    medication = display or _guess_medication_name(line)

    strength = _extract_strength(line)
    dose = _extract_dose(line, strength)
    frequency, per_day = _extract_frequency(line)
    duration, duration_days = _extract_duration(line)
    route, route_basis = _extract_route(line, dose)
    instructions = _extract_instructions(line)

    prn = bool(_PRN_RE.search(line))
    if frequency and prn:
        frequency = "%s, as needed" % frequency

    item = {
        "medication": medication,
        "display_name": display or medication,
        "normalized_name": normalized,
        "rxcui": (record or {}).get("rxcui"),
        "drug_class": (record or {}).get("drug_class"),
        "ingredients": (record or {}).get("ingredients", []),
        "common_uses": (record or {}).get("common_uses"),
        "allergy_class": (record or {}).get("allergy_class"),
        "matched_alias": alias,
        "strength": strength,
        "dose": dose,
        "frequency": frequency,
        "frequency_per_day": per_day,
        "duration": duration,
        "duration_days": duration_days,
        "route": route,
        "route_basis": route_basis,
        "instructions": instructions,
        "prn": prn,
        "raw_line": line,
        "recognized": bool(normalized),
        "normalization_source": "Local RxNorm-derived dataset" if normalized else "unrecognized",
    }
    item["needs_verification"] = [f for f in VERIFIABLE_FIELDS if not item.get(f)]
    if prn and not per_day:
        if "frequency" not in item["needs_verification"]:
            item["needs_verification"].append("doses per day (as-needed dosing)")
    item["confidence"] = _confidence(item)
    return item


def _confidence(item: dict) -> float:
    weights = {"medication": 0.30, "strength": 0.15, "dose": 0.15,
               "frequency": 0.20, "duration": 0.10, "route": 0.10}
    score = sum(w for field, w in weights.items() if item.get(field))
    if item.get("recognized"):
        score = min(1.0, score + 0.08)
    return round(score, 2)


def parse_prescription(text: str, *, use_llm: bool | None = None) -> dict:
    """Parse free-text prescription instructions.

    Returns {"items": [...], "lines": n, "errors": [...], "warnings": [...],
             "parser": {...}}
    """
    text = sanitize_text(text, 8000)
    result = {
        "items": [],
        "lines": 0,
        "errors": [],
        "warnings": [],
        "parser": {
            "engine": "regex + vocabulary",
            "spacy": spacy_status(),
            "llm_assist": False,
        },
    }
    if not text.strip():
        result["errors"].append("Prescription text is empty. Enter the instructions as written.")
        return result
    if len(text.strip()) < 4:
        result["errors"].append("Prescription text is too short to parse.")
        return result

    lines = split_medication_lines(text)
    result["lines"] = len(lines)
    if not lines:
        result["errors"].append("No medication lines could be identified in the text.")
        return result

    for line in lines:
        if _is_header_noise(line):
            result["warnings"].append(
                "Ignored a line that looks like letterhead or patient details, not a "
                "medication: '%s'. If that was wrong, edit the text." % line[:70]
            )
            continue
        item = parse_line(line)
        if not item["medication"]:
            result["warnings"].append(
                "Could not identify a medication name in: '%s'. Please check this line." % line[:80]
            )
            continue
        result["items"].append(item)

    if not result["items"]:
        result["errors"].append(
            "No medication could be identified. Write one medication per line, e.g. "
            "'Amoxicillin 500mg, take one capsule three times daily for five days.'"
        )
        return result

    use_llm = Config.llm_enabled() if use_llm is None else use_llm
    if use_llm:
        try:
            result["items"] = llm_structured_extract(text, result["items"])
            result["parser"]["llm_assist"] = True
        except Exception as exc:  # pragma: no cover - network/optional path
            log_error("LLM structured extraction", exc)
            result["warnings"].append("LLM-assisted extraction was unavailable; rule-based results are shown.")

    unknown = [i["medication"] for i in result["items"] if not i["recognized"]]
    if unknown:
        result["warnings"].append(
            "Not in the local drug dataset, so interaction checking cannot cover: %s. "
            "Verify the spelling and ask a pharmacist." % ", ".join(unknown)
        )
    for item in result["items"]:
        if item["needs_verification"]:
            result["warnings"].append(
                "%s: missing %s - confirm against the written prescription."
                % (item["medication"], ", ".join(item["needs_verification"]))
            )
    dupes = _duplicate_names(result["items"])
    for name in dupes:
        result["warnings"].append(
            "%s appears more than once in this prescription - check whether that is intended." % name
        )
    return result


_HEADER_RE = re.compile(
    r"^\s*(?:dr\.?|doctor|clinic|hospital|pharmacy|chemist|patient|name|age|sex|gender|"
    r"date|address|phone|mobile|tel|email|reg(?:n|istration)?\.?\s*no|licen[cs]e|"
    r"signature|sign|stamp|diagnosis|dept|department|follow[\s-]?up|next visit|"
    r"consultant|mbbs|md\b|rx)\b",
    re.IGNORECASE,
)


def _is_header_noise(line: str) -> bool:
    """Letterhead, patient details and footers that a scan picks up around the Rx.

    Only applied to lines with no strength, dose, frequency or duration, so a
    real medication line is never discarded.
    """
    stripped = str(line or "").strip()
    if not stripped:
        return True
    if len(stripped) <= 2:
        return True
    if not _HEADER_RE.match(stripped):
        return False
    if _STRENGTH_RE.search(stripped):
        return False
    frequency, _per_day = _extract_frequency(stripped)
    duration, _days = _extract_duration(stripped)
    if frequency or duration or _DOSE_RE.search(stripped):
        return False
    # A known medication name on the line means it is not just letterhead.
    normalized, _d, _r, _a = _match_medication(stripped, _load_vocabulary())
    return normalized is None


def _duplicate_names(items) -> list[str]:
    seen, dupes = set(), []
    for item in items:
        key = item.get("normalized_name") or (item.get("medication") or "").lower()
        if key and key in seen and key not in dupes:
            dupes.append(item.get("display_name") or item.get("medication"))
        seen.add(key)
    return dupes


def to_simple_json(items) -> list[dict]:
    """The compact shape described in the project brief."""
    out = []
    for item in items:
        out.append({
            "medication": item.get("display_name") or item.get("medication"),
            "strength": item.get("strength"),
            "dose": item.get("dose"),
            "frequency": item.get("frequency"),
            "duration": item.get("duration"),
            "route": item.get("route"),
            "instructions": item.get("instructions"),
            "needs_verification": item.get("needs_verification", []),
        })
    return out


# --------------------------------------------------------------------------
# Optional LLM assist (fills blanks only; never overwrites a parsed value)
# --------------------------------------------------------------------------
_LLM_PROMPT = (
    "Extract medication instructions from the prescription text below. Return ONLY a JSON "
    "array. Each element: {\"medication\":str,\"strength\":str|null,\"dose\":str|null,"
    "\"frequency\":str|null,\"duration\":str|null,\"route\":str|null,\"instructions\":str|null}. "
    "Copy values from the text. Use null for anything not stated. Never infer or invent a "
    "dose, strength, frequency or duration.\n\nPRESCRIPTION:\n"
)


def llm_structured_extract(text: str, items: list[dict]) -> list[dict]:
    """Ask the configured LLM for a structured reading and merge blanks only."""
    from backend.rag_engine import call_llm  # local import avoids a cycle

    raw = call_llm(_LLM_PROMPT + text, system="You extract structured data. Output JSON only.")
    if not raw:
        return items
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return items
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return items
    by_name = {}
    for entry in parsed:
        if isinstance(entry, dict) and entry.get("medication"):
            by_name[str(entry["medication"]).lower()] = entry
    for item in items:
        entry = None
        for key, candidate in by_name.items():
            name = (item.get("medication") or "").lower()
            if name and (key in name or name in key):
                entry = candidate
                break
        if not entry:
            continue
        for field in ("strength", "dose", "frequency", "duration", "route", "instructions"):
            if not item.get(field) and entry.get(field):
                item[field] = str(entry[field])
                item.setdefault("llm_filled", []).append(field)
        item["needs_verification"] = [f for f in VERIFIABLE_FIELDS if not item.get(f)]
        item["confidence"] = _confidence(item)
    return items
