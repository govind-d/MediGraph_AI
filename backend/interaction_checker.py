"""Drug interaction, allergy and condition checking.

Sources used, in order:
  1. `drug_interactions` table, seeded from `data/sample_interactions.json`
     (curated from FDA product labelling and DDInter-style severity bands).
  2. `data/allergy_cross_reactions.json` for allergy class logic.
  3. `data/condition_cautions.json` for condition-specific cautions.
  4. Duplicate-therapy detection on normalised ingredient and class.

Every finding carries its source. An empty result is reported as
"no known interaction found in the checked sources" - never as "safe".
"""
from __future__ import annotations

import json
from itertools import combinations

from database import models
from database.db import ensure_db
from utils.config import DATA_DIR
from utils.helpers import severity_sort_key
from utils.security import log_error

_ALLERGY_DATA = None
_CONDITION_DATA = None

LIMITATIONS = [
    "This check covers a curated subset of well-documented interactions, not a complete "
    "clinical interaction database.",
    "Supplements, herbal products (for example St John's wort), foods such as grapefruit, "
    "alcohol and recreational drugs are not covered.",
    "Interactions between three or more medicines acting together are not modelled.",
    "The check cannot account for your kidney or liver function, blood test results, "
    "genetics, or any medicine you did not enter.",
    "'No known interaction found' means nothing matched in the sources checked here. It is "
    "not a statement that a combination is safe.",
    "Dose checks are arithmetic against general adult maxima printed on FDA labels. They are "
    "not personalised: a prescriber may deliberately exceed a labelled maximum, and a dose "
    "inside it can still be wrong for a particular person.",
    "Food and timing guidance is general labelling advice. Follow the instruction on your own "
    "pharmacy label where the two differ.",
]


def _allergy_data():
    global _ALLERGY_DATA
    if _ALLERGY_DATA is None:
        try:
            _ALLERGY_DATA = json.loads(
                (DATA_DIR / "allergy_cross_reactions.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:  # pragma: no cover
            log_error("loading allergy dataset", exc)
            _ALLERGY_DATA = {"classes": {}}
    return _ALLERGY_DATA


def _condition_data():
    global _CONDITION_DATA
    if _CONDITION_DATA is None:
        try:
            _CONDITION_DATA = json.loads(
                (DATA_DIR / "condition_cautions.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:  # pragma: no cover
            log_error("loading condition dataset", exc)
            _CONDITION_DATA = []
    return _CONDITION_DATA


# --------------------------------------------------------------------------
# Drug-drug interactions
# --------------------------------------------------------------------------
def check_drug_pairs(items):
    """Check every pair of recognised medications. Returns (findings, checked, skipped)."""
    ensure_db()
    recognised = [i for i in items if i.get("normalized_name")]
    skipped = [
        i.get("medication") or "(unnamed)" for i in items if not i.get("normalized_name")
    ]
    findings, checked = [], []
    for a, b in combinations(recognised, 2):
        name_a, name_b = a["normalized_name"], b["normalized_name"]
        label_a = a.get("display_name") or name_a
        label_b = b.get("display_name") or name_b
        checked.append((label_a, label_b))
        if name_a == name_b:
            continue
        row = models.find_interaction(name_a, name_b)
        if row:
            findings.append({
                "type": "drug-drug",
                "drug_a": label_a,
                "drug_b": label_b,
                "severity": row["severity"],
                "mechanism": row["mechanism"],
                "description": row["description"],
                "management": row["management"],
                "source_name": row["source_name"],
                "source_url": row["source_url"],
                "evidence": "Matched pair in the curated interaction dataset",
            })
            continue
        dup = _duplicate_class_finding(a, b)
        if dup:
            findings.append(dup)
    findings.sort(key=lambda f: severity_sort_key(f["severity"]))
    return findings, checked, skipped


def _duplicate_class_finding(a, b):
    """Flag two different medicines from the same therapeutic class."""
    class_a = (a.get("drug_class") or "").lower()
    class_b = (b.get("drug_class") or "").lower()
    if not class_a or class_a != class_b:
        return None
    return {
        "type": "duplicate-therapy",
        "drug_a": a.get("display_name"),
        "drug_b": b.get("display_name"),
        "severity": "moderate",
        "mechanism": "Two medicines from the same therapeutic class (%s)." % a.get("drug_class"),
        "description": (
            "%s and %s belong to the same class. Taking both can add side effects without "
            "adding benefit, unless it is a deliberate plan."
            % (a.get("display_name"), b.get("display_name"))
        ),
        "management": "Confirm with the prescriber or pharmacist that both are intended.",
        "source_name": "MediGraph AI duplicate-therapy rule (drug class from the local dataset)",
        "source_url": None,
        "evidence": "Class match: %s" % a.get("drug_class"),
    }


# --------------------------------------------------------------------------
# Allergies
# --------------------------------------------------------------------------
def _aliases_for(item):
    aliases = {str(item.get("normalized_name") or "").lower(),
               str(item.get("display_name") or "").lower(),
               str(item.get("medication") or "").lower()}
    aliases.update(str(i).lower() for i in item.get("ingredients") or [])
    display = str(item.get("display_name") or "")
    if "(" in display:
        base, _, inner = display.partition("(")
        aliases.add(base.strip().lower())
        aliases.add(inner.replace(")", "").strip().lower())
    return {a for a in aliases if a}


def check_allergies(items, allergies):
    """Compare prescribed medications against recorded allergies."""
    classes = _allergy_data().get("classes", {})
    findings = []
    for allergy in allergies:
        substance = str(allergy.get("substance") or "").strip().lower()
        if not substance:
            continue
        recorded_reaction = allergy.get("reaction")
        # Which classes does the recorded allergy belong to?
        allergy_classes = {
            key for key, meta in classes.items()
            if substance == key or substance in [m.lower() for m in meta.get("members", [])]
        }
        for item in items:
            aliases = _aliases_for(item)
            item_class = str(item.get("allergy_class") or "").lower()
            name = item.get("display_name") or item.get("medication")

            if substance in aliases:
                findings.append(_allergy_finding(
                    "high", name, allergy, substance,
                    "This medicine is the substance recorded as an allergy for this patient.",
                    "Do not take it. Contact the prescriber or pharmacist before the next dose.",
                    "Direct name match against the recorded allergy",
                    classes.get(item_class, {}),
                ))
                continue

            # Same class as the recorded allergy
            shared = allergy_classes & ({item_class} if item_class else set())
            if shared:
                cls = classes.get(next(iter(shared)), {})
                findings.append(_allergy_finding(
                    "high", name, allergy, substance,
                    "%s is in the same class (%s) as the recorded allergy '%s'."
                    % (name, cls.get("label", item_class), substance),
                    "Usually avoided. The prescriber needs to know about this allergy before "
                    "the medicine is taken.",
                    "Class membership: %s" % cls.get("label", item_class),
                    cls,
                ))
                continue

            # Cross-reactive class
            cross = set()
            for key in allergy_classes:
                cross.update(c.lower() for c in classes.get(key, {}).get("cross_reactive_classes", []))
            if item_class and item_class in cross:
                cls = classes.get(item_class, {})
                source_cls = classes.get(next(iter(allergy_classes)), {}) if allergy_classes else {}
                findings.append(_allergy_finding(
                    "moderate", name, allergy, substance,
                    "%s belongs to %s, which can cross-react with the recorded '%s' allergy."
                    % (name, cls.get("label", item_class), substance),
                    "Tell the prescriber about the allergy. Whether this medicine is suitable "
                    "is a clinical decision, not one to make alone.",
                    "Cross-reactivity between %s and %s"
                    % (source_cls.get("label", substance), cls.get("label", item_class)),
                    source_cls or cls,
                ))
    findings.sort(key=lambda f: severity_sort_key(f["severity"]))
    return findings


def _allergy_finding(severity, med_name, allergy, substance, description, management,
                     evidence, cls_meta):
    reaction = allergy.get("reaction")
    if reaction:
        description += " Recorded reaction: %s." % reaction
    return {
        "type": "allergy",
        "medication": med_name,
        "allergy": substance,
        "severity": severity,
        "description": description,
        "management": management,
        "evidence": evidence,
        "mechanism": cls_meta.get("note"),
        "source_name": cls_meta.get("source_name", "Patient-recorded allergy in MediGraph AI"),
        "source_url": cls_meta.get("source_url"),
    }


# --------------------------------------------------------------------------
# Conditions (including pregnancy)
# --------------------------------------------------------------------------
def check_conditions(items, conditions, *, is_pregnant=False, age=None):
    findings = []
    names = [str(c.get("condition_name") or "").strip().lower()
             for c in conditions if str(c.get("status", "active")).lower() == "active"]
    if is_pregnant:
        names.append("pregnancy")
    for rule in _condition_data():
        condition = str(rule.get("condition", "")).lower()
        if not any(condition in n or n in condition for n in names if n):
            continue
        rule_drugs = {d.lower() for d in rule.get("drugs", [])}
        rule_classes = [c.lower() for c in rule.get("drug_class_match", [])]
        for item in items:
            aliases = _aliases_for(item)
            item_class = str(item.get("allergy_class") or "").lower()
            drug_class = str(item.get("drug_class") or "").lower()
            hit = bool(aliases & rule_drugs)
            if not hit and rule_classes:
                hit = any(rc in drug_class or rc == item_class for rc in rule_classes)
            if not hit:
                continue
            findings.append({
                "type": "condition",
                "medication": item.get("display_name") or item.get("medication"),
                "condition": rule.get("condition"),
                "severity": rule.get("severity", "moderate"),
                "description": rule.get("description"),
                "management": rule.get("management"),
                "evidence": "Recorded condition '%s' matched against labelling caution"
                            % rule.get("condition"),
                "source_name": rule.get("source_name"),
                "source_url": rule.get("source_url"),
            })
    if age is not None and age >= 65:
        for item in items:
            if str(item.get("allergy_class") or "").lower() == "nsaid":
                findings.append({
                    "type": "condition",
                    "medication": item.get("display_name") or item.get("medication"),
                    "condition": "age 65 or over",
                    "severity": "moderate",
                    "description": (
                        "NSAID-related stomach bleeding and kidney effects are more common from "
                        "age 65 onwards, even at normal doses."
                    ),
                    "management": "Ask whether a lower dose, a shorter course, stomach protection "
                                  "or a different painkiller is more appropriate.",
                    "evidence": "Patient age recorded as %s" % age,
                    "source_name": "FDA NSAID class labelling (Warnings - geriatric use)",
                    "source_url": "https://www.fda.gov/drugs/drug-safety-and-availability",
                })
    if age is not None and age < 18:
        for item in items:
            if (item.get("normalized_name") or "") == "aspirin":
                findings.append({
                    "type": "condition",
                    "medication": item.get("display_name"),
                    "condition": "age under 18",
                    "severity": "high",
                    "description": (
                        "Aspirin is linked to Reye's syndrome in children and teenagers, "
                        "particularly during viral illness, and is normally avoided under 18 "
                        "unless specifically prescribed for a condition such as Kawasaki disease."
                    ),
                    "management": "Confirm with the prescriber that aspirin is intended for a "
                                  "patient of this age.",
                    "evidence": "Patient age recorded as %s" % age,
                    "source_name": "FDA aspirin labelling (Reye's syndrome warning)",
                    "source_url": "https://www.fda.gov/drugs/drug-safety-and-availability",
                })
    findings.sort(key=lambda f: severity_sort_key(f["severity"]))
    return findings


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def run_full_check(items, *, allergies=None, conditions=None, is_pregnant=False, age=None,
                   weight_kg=None):
    """Full safety check for a set of parsed (and normalised) medication items."""
    from backend import dose_checker  # local import keeps the module graph shallow

    interactions, checked, skipped = check_drug_pairs(items)
    allergy_findings = check_allergies(items, allergies or [])
    condition_findings = check_conditions(
        items, conditions or [], is_pregnant=is_pregnant, age=age
    )
    dose_findings = dose_checker.check_doses(items, age=age, weight_kg=weight_kg)
    timing = dose_checker.timing_guidance(items)
    timing_conflicts = dose_checker.conflicting_timing(items)

    all_sev = [f["severity"] for f in interactions + allergy_findings + condition_findings]
    all_sev += [f["severity"] for f in dose_findings if f["severity"] != "none"]
    highest = sorted(all_sev, key=severity_sort_key)[0] if all_sev else "none"
    return {
        "interactions": interactions,
        "allergy_warnings": allergy_findings,
        "condition_warnings": condition_findings,
        "dose_warnings": dose_findings,
        "timing_guidance": timing,
        "timing_conflicts": timing_conflicts,
        "checked_pairs": checked,
        "unchecked_medications": skipped,
        "highest_severity": highest,
        "counts": {
            "high": sum(1 for s in all_sev if s == "high"),
            "moderate": sum(1 for s in all_sev if s == "moderate"),
            "informational": sum(1 for s in all_sev if s == "informational"),
        },
        "limitations": LIMITATIONS,
        "sources_checked": [
            "Curated interaction dataset derived from FDA product labelling and "
            "DDInter-style severity bands (local)",
            "Allergy class / cross-reactivity dataset (local)",
            "Condition caution dataset derived from FDA labelling (local)",
            "Labelled adult maximum daily doses derived from FDA labelling (local)",
            "Food, alcohol and spacing rules derived from FDA labelling (local)",
        ],
    }


def severity_summary(result) -> str:
    counts = result.get("counts", {})
    if counts.get("high"):
        return "high"
    if counts.get("moderate"):
        return "moderate"
    if counts.get("informational"):
        return "informational"
    return "none"
