"""Medication name normalisation.

Default path  : local RxNorm-derived dataset in `data/sample_medications.json`
                plus fuzzy matching for typos.
Optional path : live RxNorm (RxNav) lookup when USE_RXNORM_API=true, and
                openFDA label extracts when USE_OPENFDA_API=true.

Every returned record carries `source` so the UI can distinguish a verified
API result from a local dataset result.
"""
from __future__ import annotations

import difflib
import json
import urllib.error
import urllib.parse
import urllib.request

from backend.parser import _load_vocabulary
from utils.config import DATA_DIR, Config
from utils.security import log_error

_LOCAL_RECORDS = None
_API_CACHE: dict = {}


def _records():
    global _LOCAL_RECORDS
    if _LOCAL_RECORDS is None:
        try:
            _LOCAL_RECORDS = json.loads(
                (DATA_DIR / "sample_medications.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:  # pragma: no cover
            log_error("loading medication dataset", exc)
            _LOCAL_RECORDS = []
    return _LOCAL_RECORDS


def _http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "MediGraph-AI/1.0"})
    with urllib.request.urlopen(req, timeout=Config.HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Local normalisation
# --------------------------------------------------------------------------
def normalize_local(name: str):
    """Exact or fuzzy match against the local vocabulary."""
    if not name:
        return None
    vocab = _load_vocabulary()
    key = name.strip().lower()
    if key in vocab:
        info = vocab[key]
        return _record_payload(info["record"], matched_on=key, method="exact")
    # Strip dosage-form noise and try again
    stripped = key.replace("tablet", "").replace("capsule", "").strip(" .-")
    if stripped in vocab:
        info = vocab[stripped]
        return _record_payload(info["record"], matched_on=stripped, method="exact")
    close = difflib.get_close_matches(key, list(vocab.keys()), n=1, cutoff=0.86)
    if close:
        info = vocab[close[0]]
        return _record_payload(info["record"], matched_on=close[0], method="fuzzy")
    return None


def _record_payload(record: dict, matched_on: str, method: str) -> dict:
    return {
        "normalized_name": record.get("normalized_name"),
        "display_name": record.get("display_name"),
        "rxcui": record.get("rxcui"),
        "drug_class": record.get("drug_class"),
        "ingredients": record.get("ingredients", []),
        "common_uses": record.get("common_uses"),
        "allergy_class": record.get("allergy_class"),
        "brand_names": record.get("brand_names", []),
        "matched_on": matched_on,
        "match_method": method,
        "source": "Local RxNorm-derived dataset (simulated lookup)",
        "verified_external": False,
    }


# --------------------------------------------------------------------------
# Live RxNorm lookup (optional)
# --------------------------------------------------------------------------
def rxnorm_lookup(name: str):
    """Resolve a name to an RxNorm ingredient via RxNav. Returns None on failure."""
    if not Config.USE_RXNORM_API or not name:
        return None
    key = name.strip().lower()
    if key in _API_CACHE:
        return _API_CACHE[key]
    try:
        url = "%s/rxcui.json?%s" % (
            Config.RXNORM_BASE_URL,
            urllib.parse.urlencode({"name": name, "search": 2}),
        )
        data = _http_get_json(url)
        ids = (data.get("idGroup") or {}).get("rxnormId") or []
        if not ids:
            approx = _http_get_json(
                "%s/approximateTerm.json?%s"
                % (Config.RXNORM_BASE_URL, urllib.parse.urlencode({"term": name, "maxEntries": 1}))
            )
            cands = (approx.get("approximateGroup") or {}).get("candidate") or []
            ids = [cands[0]["rxcui"]] if cands else []
        if not ids:
            _API_CACHE[key] = None
            return None
        rxcui = ids[0]
        props = _http_get_json("%s/rxcui/%s/properties.json" % (Config.RXNORM_BASE_URL, rxcui))
        prop = props.get("properties") or {}
        payload = {
            "normalized_name": (prop.get("name") or name).lower(),
            "display_name": prop.get("name") or name,
            "rxcui": rxcui,
            "drug_class": prop.get("tty"),
            "ingredients": [],
            "common_uses": None,
            "allergy_class": None,
            "brand_names": [],
            "matched_on": name.lower(),
            "match_method": "rxnorm-api",
            "source": "RxNorm (RxNav API), National Library of Medicine",
            "source_url": "https://rxnav.nlm.nih.gov/REST/rxcui/%s" % rxcui,
            "verified_external": True,
        }
        _API_CACHE[key] = payload
        return payload
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        log_error("RxNorm lookup", exc)
        _API_CACHE[key] = None
        return None


# --------------------------------------------------------------------------
# openFDA label extract (optional)
# --------------------------------------------------------------------------
def openfda_label(name: str, sections=("indications_and_usage", "warnings", "drug_interactions")):
    """Fetch short extracts from the openFDA drug label API."""
    if not Config.USE_OPENFDA_API or not name:
        return None
    cache_key = "fda:%s" % name.lower()
    if cache_key in _API_CACHE:
        return _API_CACHE[cache_key]
    try:
        params = {
            "search": 'openfda.generic_name:"%s"' % name,
            "limit": 1,
        }
        if Config.OPENFDA_API_KEY:
            params["api_key"] = Config.OPENFDA_API_KEY
        data = _http_get_json("%s?%s" % (Config.OPENFDA_BASE_URL, urllib.parse.urlencode(params)))
        results = data.get("results") or []
        if not results:
            _API_CACHE[cache_key] = None
            return None
        label = results[0]
        payload = {
            "source": "openFDA drug label API",
            "source_url": "https://open.fda.gov/apis/drug/label/",
            "verified_external": True,
            "sections": {},
        }
        for section in sections:
            value = label.get(section)
            if value:
                text = value[0] if isinstance(value, list) else str(value)
                payload["sections"][section] = text[:1200]
        _API_CACHE[cache_key] = payload
        return payload
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        log_error("openFDA label lookup", exc)
        _API_CACHE[cache_key] = None
        return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def normalize(name: str):
    """Best available normalisation for a single medication name."""
    local = normalize_local(name)
    api = rxnorm_lookup(name)
    if local and api:
        merged = dict(local)
        merged.update({
            "rxcui": api.get("rxcui") or local.get("rxcui"),
            "source": "%s; confirmed against %s" % (local["source"], api["source"]),
            "source_url": api.get("source_url"),
            "verified_external": True,
        })
        return merged
    return api or local


def normalize_items(items):
    """Normalise every parsed prescription item in place and return it."""
    for item in items:
        name = item.get("medication") or ""
        payload = normalize(name)
        if not payload:
            item["recognized"] = False
            item["normalization_source"] = "not found in the checked sources"
            continue
        item["normalized_name"] = payload["normalized_name"]
        item["display_name"] = payload.get("display_name") or item.get("display_name") or name
        item["rxcui"] = payload.get("rxcui")
        item["drug_class"] = payload.get("drug_class")
        item["ingredients"] = payload.get("ingredients") or []
        item["common_uses"] = payload.get("common_uses")
        item["allergy_class"] = payload.get("allergy_class")
        item["recognized"] = True
        item["match_method"] = payload.get("match_method")
        item["normalization_source"] = payload.get("source")
        item["normalization_source_url"] = payload.get("source_url")
        item["verified_external"] = payload.get("verified_external", False)
        if payload.get("match_method") == "fuzzy":
            item.setdefault("notes", []).append(
                "Name matched approximately to '%s' - confirm the spelling." % payload.get("display_name")
            )
    return items


def medication_info(normalized_name: str):
    """Reference details for one medication (local dataset + optional openFDA)."""
    for record in _records():
        if record.get("normalized_name") == (normalized_name or "").lower():
            info = dict(record)
            label = openfda_label(record.get("normalized_name"))
            if label:
                info["fda_label"] = label
            return info
    return None


def known_medication_names():
    return sorted(r.get("display_name") or r.get("normalized_name") for r in _records())
