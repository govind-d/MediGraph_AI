"""Dosage validation and food/timing guidance (MSE-2 deck features)."""
from __future__ import annotations

from backend import dose_checker, interaction_checker, medication_normalizer, parser, pipeline


def _items(text):
    return medication_normalizer.normalize_items(parser.parse_prescription(text)["items"])


# --------------------------------------------------------------------------
# Dose arithmetic
# --------------------------------------------------------------------------
def test_strength_conversion_to_mg():
    assert dose_checker.strength_in_mg("500mg") == 500
    assert dose_checker.strength_in_mg("1g") == 1000
    assert dose_checker.strength_in_mg("50 mcg") == 0.05
    assert dose_checker.strength_in_mg("0.4mg") == 0.4
    assert dose_checker.strength_in_mg("250mg/5ml") is None  # concentration needs a volume
    assert dose_checker.strength_in_mg(None) is None


def test_units_per_dose():
    assert dose_checker.units_per_dose("1 capsule") == 1
    assert dose_checker.units_per_dose("2 tablets") == 2
    assert dose_checker.units_per_dose("one tablet") == 1
    assert dose_checker.units_per_dose("5 ml") is None


def test_daily_total_is_computed_and_explained():
    item = _items("Amoxicillin 500mg, one capsule three times daily for 5 days")[0]
    total, per_dose, explanation = dose_checker.daily_total_mg(item)
    assert total == 1500 and per_dose == 500
    assert "1500 mg per day" in explanation


def test_daily_total_refuses_to_guess_when_frequency_is_missing():
    item = _items("Omeprazole 20mg")[0]
    total, _per_dose, reason = dose_checker.daily_total_mg(item)
    assert total is None
    assert "does not state how many doses per day" in reason


# --------------------------------------------------------------------------
# Limit checks
# --------------------------------------------------------------------------
def test_dose_within_the_labelled_maximum_is_not_an_alert():
    findings = dose_checker.check_doses(_items("Amoxicillin 500mg, one capsule three times daily"))
    assert [f["severity"] for f in findings] == ["none"]
    assert "within the labelled adult maximum" in findings[0]["description"]


def test_dose_above_the_daily_maximum_is_high_concern():
    findings = dose_checker.check_doses(
        _items("Acetaminophen 1000mg, take 2 tablets four times a day")
    )
    alert = [f for f in findings if f["severity"] == "high"]
    assert alert, findings
    assert alert[0]["calculated_daily_mg"] == 8000
    assert alert[0]["limit_daily_mg"] == 4000
    assert "parsing or typing error" in alert[0]["management"]
    assert alert[0]["source_name"]


def test_single_dose_above_the_label_maximum_is_moderate():
    findings = dose_checker.check_doses(_items("Ibuprofen 1200mg, one tablet twice daily"))
    moderate = [f for f in findings if f["severity"] == "moderate"]
    assert moderate and "single-dose maximum" in moderate[0]["description"]


def test_duplicate_ingredient_totals_are_added_together():
    findings = dose_checker.check_doses(
        _items("Acetaminophen 1000mg three times daily\nParacetamol 500mg three times daily")
    )
    cumulative = [f for f in findings if "lines of this prescription" in f["description"]]
    assert cumulative and cumulative[0]["severity"] == "high"
    assert cumulative[0]["calculated_daily_mg"] == 4500


def test_unknown_limit_reports_the_calculation_without_judging_it():
    findings = dose_checker.check_doses(_items("Salbutamol 100mcg, 2 puffs four times a day"))
    assert findings and findings[0]["severity"] == "informational"
    assert "No labelled maximum" in findings[0]["description"]


def test_paediatric_mg_per_kg_check(patient):
    items = _items("Acetaminophen 500mg, 2 tablets four times a day")
    findings = dose_checker.check_doses(items, age=8, weight_kg=25)
    paediatric = [f for f in findings if "mg/kg" in f["description"]]
    assert paediatric and paediatric[0]["severity"] == "high"


# --------------------------------------------------------------------------
# Food / timing
# --------------------------------------------------------------------------
def test_food_rules_are_returned_with_reason_and_source():
    guidance = dose_checker.timing_guidance(_items("Levothyroxine 50mcg once daily"))
    assert guidance
    rule = guidance[0]
    assert "empty stomach" in rule["guidance"].lower()
    assert rule["reason"] and rule["source_name"]


def test_spacing_rule_for_fluoroquinolones():
    guidance = dose_checker.timing_guidance(_items("Ciprofloxacin 500mg twice daily for 5 days"))
    spacing = [g for g in guidance if g["rule_type"] == "spacing"]
    assert spacing and spacing[0]["spacing_hours"] == 2
    assert "dairy" in spacing[0]["spacing_with"]


def test_class_level_match_catches_any_nsaid():
    guidance = dose_checker.timing_guidance(_items("Naproxen 250mg twice daily"))
    assert any("with or just after food" in g["guidance"] for g in guidance)


def test_alcohol_avoidance_rules():
    guidance = dose_checker.timing_guidance(_items("Metronidazole 400mg three times daily for 7 days"))
    assert any("alcohol" in g["guidance"].lower() and g["rule_type"] == "avoid" for g in guidance)


def test_conflicting_food_rules_are_surfaced():
    conflicts = dose_checker.conflicting_timing(
        _items("Levothyroxine 50mcg once daily\nIbuprofen 400mg three times daily")
    )
    assert any("empty stomach" in c["description"] for c in conflicts)


# --------------------------------------------------------------------------
# Integration
# --------------------------------------------------------------------------
def test_full_check_includes_dose_and_timing_sections():
    result = interaction_checker.run_full_check(
        _items("Acetaminophen 1000mg, take 2 tablets four times a day\n"
               "Ciprofloxacin 500mg twice daily for 5 days")
    )
    assert result["dose_warnings"] and result["timing_guidance"]
    assert result["highest_severity"] == "high"
    assert any("maximum daily doses" in s for s in result["sources_checked"])


def test_report_renders_the_new_sections(patient):
    from backend import safety_report

    outcome = pipeline.analyze("Acetaminophen 1000mg, take 2 tablets four times a day",
                               patient, save=False)
    report = outcome["report"]
    assert report["dose_warnings"] and report["timing_guidance"] is not None
    markdown = safety_report.to_markdown(report)
    assert "Dosage check" in markdown
    assert "Food, alcohol and timing guidance" in markdown


def test_severity_tiers_map_to_clinical_names():
    from utils.helpers import severity_full, severity_tier

    assert severity_tier("high") == "Major"
    assert severity_tier("moderate") == "Moderate"
    assert severity_tier("informational") == "Minor"
    assert severity_full("high") == "High concern (Major)"


def test_schedule_page_timing_guidance(patient):
    from backend import scheduler

    pipeline.analyze("Ciprofloxacin 500mg twice daily for 5 days", patient,
                     save=True, build_schedule=True)
    guidance = scheduler.timing_guidance_for_patient(patient["id"])
    assert any(g["rule_type"] == "spacing" for g in guidance)
