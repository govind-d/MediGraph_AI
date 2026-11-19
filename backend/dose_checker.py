"""Dosage validation and food/timing guidance.

Dosage validation is deliberately arithmetic and transparent:

    daily total = strength x units per dose x doses per day

It is compared against the adult maximum printed on the FDA label (and, for a
child, against mg/kg/day figures when weight is recorded). Every result says
which numbers were used, and nothing is checked unless the prescription
actually stated the strength, the dose and the frequency - a missing field
produces "could not be checked", never an assumption.

These limits are general labelled ceilings, not personalised limits. A
prescriber may legitimately exceed them, and a safe dose for one person can be
unsafe for another, so every finding points back to a clinician.
"""
from __future__ import annotations

import json
import re

from utils.config import DATA_DIR
from utils.security import log_error

_LIMITS = None
_RULES = None

_UNIT_TO_MG = {"mg": 1.0, "g": 1000.0, "gm": 1000.0, "gram": 1000.0, "grams": 1000.0,
               "mcg": 0.001, "ug": 0.001, "µg": 0.001}

_STRENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|ug|µg|g|gm|grams?)\b", re.IGNORECASE)
_QTY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")
_WORD_QTY = {"half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _limits():
    global _LIMITS
    if _LIMITS is None:
        try:
            _LIMITS = json.loads((DATA_DIR / "dose_limits.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover
            log_error("loading dose limits", exc)
            _LIMITS = {"limits": {}, "pediatric_mg_per_kg_day": {}}
    return _LIMITS


def _rules():
    global _RULES
    if _RULES is None:
        try:
            _RULES = json.loads((DATA_DIR / "food_timing_rules.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover
            log_error("loading food/timing rules", exc)
            _RULES = []
    return _RULES


# --------------------------------------------------------------------------
# Dose arithmetic
# --------------------------------------------------------------------------
def strength_in_mg(strength_text):
    """Convert '500mg', '0.4 mg', '50 mcg', '1g' to milligrams. None if not parseable."""
    if not strength_text:
        return None
    # Ignore concentrations such as 250mg/5ml - the dose then needs the volume.
    if "/" in str(strength_text):
        return None
    match = _STRENGTH_RE.search(str(strength_text))
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    factor = _UNIT_TO_MG.get(unit)
    return round(value * factor, 6) if factor else None


def units_per_dose(dose_text):
    """'1 capsule' -> 1.0, '2 tablets' -> 2.0, 'one tablet' -> 1.0. None if unclear."""
    if not dose_text:
        return None
    text = str(dose_text).strip().lower()
    if not re.search(r"\b(tablet|tab|capsule|cap|pill|puff|sachet|patch)s?\b", text):
        return None
    match = _QTY_RE.match(text)
    if match:
        return float(match.group(1))
    first = text.split()[0]
    return float(_WORD_QTY[first]) if first in _WORD_QTY else None


def daily_total_mg(item):
    """Return (total_mg, per_dose_mg, explanation) or (None, None, reason)."""
    strength = strength_in_mg(item.get("strength"))
    if strength is None:
        return None, None, "the strength was not stated in a form that can be totalled"
    per_day = item.get("frequency_per_day")
    if not per_day:
        return None, None, "the prescription does not state how many doses per day"
    units = units_per_dose(item.get("dose"))
    if units is None:
        units = 1.0
        basis = "assuming one unit of the stated strength per dose"
    else:
        basis = "%s unit(s) per dose" % _fmt(units)
    per_dose_mg = strength * units
    total = per_dose_mg * float(per_day)
    explanation = "%s mg x %s x %s dose(s) per day = %s mg per day (%s)" % (
        _fmt(strength), _fmt(units), _fmt(per_day), _fmt(total), basis
    )
    return round(total, 4), round(per_dose_mg, 4), explanation


def _fmt(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else ("%.2f" % value).rstrip("0").rstrip(".")


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_doses(items, *, age=None, weight_kg=None):
    """Validate each medication's daily total against labelled maxima.

    Returns a list of findings, each with severity high/moderate/informational.
    """
    findings = []
    limits = _limits()["limits"]
    paediatric = _limits().get("pediatric_mg_per_kg_day", {})

    # Cumulative totals per ingredient, so the same drug in two lines is caught.
    totals: dict = {}

    for item in items:
        name = item.get("display_name") or item.get("medication")
        key = (item.get("normalized_name") or "").lower()
        limit = limits.get(key)
        total, per_dose, explanation = daily_total_mg(item)

        if total is not None and key:
            entry = totals.setdefault(key, {"name": name, "total": 0.0, "lines": 0})
            entry["total"] += total
            entry["lines"] += 1

        if not limit:
            if key and total is not None:
                findings.append({
                    "type": "dose",
                    "medication": name,
                    "severity": "informational",
                    "calculated_daily_mg": total,
                    "limit_daily_mg": None,
                    "description": "Calculated daily total: %s. No labelled maximum for this "
                                   "medicine is held in the local dataset, so the total could not "
                                   "be compared." % explanation,
                    "management": "Ask your pharmacist to confirm the total daily dose is right "
                                  "for you.",
                    "evidence": explanation,
                    "source_name": "MediGraph AI dose arithmetic (no reference maximum available)",
                    "source_url": None,
                })
            continue

        if total is None:
            findings.append({
                "type": "dose",
                "medication": name,
                "severity": "informational",
                "calculated_daily_mg": None,
                "limit_daily_mg": limit.get("max_daily_mg"),
                "description": "The daily dose could not be checked because %s. The labelled adult "
                               "maximum for %s is %s mg per day." % (explanation, name,
                                                                     _fmt(limit["max_daily_mg"])),
                "management": "Confirm the missing details against the written prescription or "
                              "pharmacy label, then compare with your pharmacist.",
                "evidence": "Missing information prevented the calculation",
                "source_name": limit.get("source_name"),
                "source_url": limit.get("source_url"),
            })
            continue

        max_daily = float(limit["max_daily_mg"])
        max_single = float(limit.get("max_single_mg") or max_daily)
        note = limit.get("note")

        if total > max_daily * 1.0001:
            findings.append({
                "type": "dose",
                "medication": name,
                "severity": "high",
                "calculated_daily_mg": total,
                "limit_daily_mg": max_daily,
                "description": "The daily total worked out from this prescription (%s mg) is above "
                               "the labelled adult maximum of %s mg per day. %s"
                               % (_fmt(total), _fmt(max_daily), note or ""),
                "management": "Do not change anything yourself. Check the figures against the "
                              "written prescription first - a parsing or typing error is the most "
                              "likely explanation - then contact the prescriber or pharmacist "
                              "before the next dose.",
                "evidence": explanation,
                "source_name": limit.get("source_name"),
                "source_url": limit.get("source_url"),
            })
        elif per_dose and per_dose > max_single * 1.0001:
            findings.append({
                "type": "dose",
                "medication": name,
                "severity": "moderate",
                "calculated_daily_mg": total,
                "limit_daily_mg": max_daily,
                "description": "Each dose works out as %s mg, above the %s mg single-dose maximum "
                               "on the label, although the daily total (%s mg) is within the "
                               "%s mg daily limit. %s"
                               % (_fmt(per_dose), _fmt(max_single), _fmt(total),
                                  _fmt(max_daily), note or ""),
                "management": "Confirm the per-dose amount with your pharmacist.",
                "evidence": explanation,
                "source_name": limit.get("source_name"),
                "source_url": limit.get("source_url"),
            })
        else:
            at_ceiling = total >= max_daily * 0.95
            extra = (" This sits at the labelled maximum, which for many medicines is also the "
                     "normal dose - leave no room for an extra over-the-counter product "
                     "containing the same ingredient." if at_ceiling else "")
            findings.append({
                "type": "dose",
                "medication": name,
                "severity": "none",
                "calculated_daily_mg": total,
                "limit_daily_mg": max_daily,
                "description": "Daily total %s mg, within the labelled adult maximum of %s mg per "
                               "day.%s This is an arithmetic check only - it does not mean the "
                               "dose is right for you." % (_fmt(total), _fmt(max_daily), extra),
                "management": "No action implied; your prescriber sets the dose for your situation.",
                "evidence": explanation,
                "source_name": limit.get("source_name"),
                "source_url": limit.get("source_url"),
            })

        if age is not None and age < 18 and weight_kg and key in paediatric:
            rule = paediatric[key]
            per_kg = total / float(weight_kg)
            if per_kg > float(rule["max"]):
                findings.append({
                    "type": "dose",
                    "medication": name,
                    "severity": "high",
                    "calculated_daily_mg": total,
                    "limit_daily_mg": float(rule["max"]) * float(weight_kg),
                    "description": "For a child of %s kg this works out as %s mg/kg per day, above "
                                   "the %s mg/kg per day figure in the paediatric labelling. %s"
                                   % (_fmt(weight_kg), _fmt(per_kg), _fmt(rule["max"]),
                                      rule.get("note", "")),
                    "management": "Check the figures against the written prescription and contact "
                                  "the prescriber before the next dose.",
                    "evidence": "%s; patient weight %s kg" % (explanation, _fmt(weight_kg)),
                    "source_name": rule.get("source_name"),
                    "source_url": None,
                })

    # Duplicate ingredient across several lines
    for key, entry in totals.items():
        limit = limits.get(key)
        if entry["lines"] < 2 or not limit:
            continue
        if entry["total"] > float(limit["max_daily_mg"]) * 1.0001:
            findings.append({
                "type": "dose",
                "medication": entry["name"],
                "severity": "high",
                "calculated_daily_mg": entry["total"],
                "limit_daily_mg": float(limit["max_daily_mg"]),
                "description": "%s appears on %d lines of this prescription. Added together they "
                               "come to %s mg per day, above the labelled maximum of %s mg."
                               % (entry["name"], entry["lines"], _fmt(entry["total"]),
                                  _fmt(limit["max_daily_mg"])),
                "management": "Check whether both lines are meant to be taken. Contact the "
                              "prescriber or pharmacist before taking both.",
                "evidence": "Cumulative total across %d prescription lines" % entry["lines"],
                "source_name": limit.get("source_name"),
                "source_url": limit.get("source_url"),
            })
    return findings


def timing_guidance(items):
    """Food, spacing and avoidance rules for the medications in a prescription."""
    out = []
    for item in items:
        aliases = {str(item.get("normalized_name") or "").lower(),
                   str(item.get("medication") or "").lower()}
        aliases.update(str(i).lower() for i in item.get("ingredients") or [])
        allergy_class = str(item.get("allergy_class") or "").lower()
        drug_class = str(item.get("drug_class") or "").lower()
        name = item.get("display_name") or item.get("medication")
        for rule in _rules():
            drugs = {d.lower() for d in rule.get("drugs", [])}
            classes = [c.lower() for c in rule.get("drug_class_match", [])]
            hit = bool(aliases & drugs)
            if not hit and classes:
                hit = any(c == allergy_class or c in drug_class for c in classes)
            if not hit:
                continue
            out.append({
                "medication": name,
                "rule_type": rule.get("rule_type"),
                "guidance": rule.get("guidance"),
                "reason": rule.get("reason"),
                "spacing_hours": rule.get("spacing_hours"),
                "spacing_with": rule.get("spacing_with"),
                "written_instruction": item.get("instructions"),
                "source_name": rule.get("source_name"),
                "source_url": rule.get("source_url"),
            })
    return out


def conflicting_timing(items):
    """Pairs whose timing rules pull in opposite directions (e.g. empty stomach vs with food)."""
    guidance = timing_guidance(items)
    empty = [g for g in guidance if "empty stomach" in (g["guidance"] or "").lower()]
    spaced = [g for g in guidance if g.get("spacing_hours")]
    conflicts = []
    for a in empty:
        for b in guidance:
            if b["medication"] == a["medication"]:
                continue
            if re.search(r"with (?:or just after )?(?:food|meals?|a meal)",
                         (b["guidance"] or ""), re.IGNORECASE):
                conflicts.append({
                    "medications": [a["medication"], b["medication"]],
                    "description": "%s is normally taken on an empty stomach while %s is normally "
                                   "taken with food, so they cannot be taken at the same moment."
                                   % (a["medication"], b["medication"]),
                    "management": "Ask your pharmacist to lay out times that satisfy both.",
                })
    for g in spaced:
        conflicts.append({
            "medications": [g["medication"]],
            "description": "%s needs a gap of about %s hours from %s."
                           % (g["medication"], g["spacing_hours"], g["spacing_with"]),
            "management": "Build the gap into your daily routine; a pharmacist can suggest times.",
        })
    return conflicts
