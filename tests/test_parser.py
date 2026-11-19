"""Requirement 1 & 6: prescription text extraction and missing-information handling."""
from __future__ import annotations

from backend import parser


def test_brief_example_parses_to_the_documented_shape():
    result = parser.parse_prescription(
        "Amoxicillin 500mg, take one capsule three times daily for five days."
    )
    assert result["errors"] == []
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["medication"] == "Amoxicillin"
    assert item["strength"] == "500mg"
    assert item["dose"] == "1 capsule"
    assert item["frequency"] == "3 times daily"
    assert item["frequency_per_day"] == 3
    assert item["duration"] == "5 days"
    assert item["duration_days"] == 5
    assert item["route"] == "oral"
    assert item["needs_verification"] == []


def test_simple_json_shape():
    result = parser.parse_prescription("Amoxicillin 500mg, 3 times a day, for 5 days.")
    simple = parser.to_simple_json(result["items"])[0]
    assert set(simple) >= {"medication", "strength", "dose", "frequency", "duration", "route"}
    assert simple["medication"] == "Amoxicillin"
    assert simple["frequency"] == "3 times daily"
    assert simple["duration"] == "5 days"


def test_multiple_medications_split_across_lines():
    text = ("Warfarin 5 mg once daily at night.\n"
            "Ibuprofen 400 mg tablet, take one tablet three times a day after food for 7 days.")
    result = parser.parse_prescription(text)
    names = [i["medication"] for i in result["items"]]
    assert names == ["Warfarin", "Ibuprofen"]
    assert result["items"][1]["instructions"] and "after food" in result["items"][1]["instructions"].lower()


def test_multiple_medications_on_one_line():
    result = parser.parse_prescription("Metformin 500mg BD and Atorvastatin 10mg at night")
    names = sorted(i["normalized_name"] for i in result["items"])
    assert names == ["atorvastatin", "metformin"]


def test_abbreviations_po_bd_hs_od_and_x_days():
    result = parser.parse_prescription(
        "Tab Metformin 500mg PO BD x 30 days\nTab Atorvastatin 10mg PO HS\nTab Amlodipine 5mg OD"
    )
    items = {i["normalized_name"]: i for i in result["items"]}
    assert items["metformin"]["frequency_per_day"] == 2
    assert items["metformin"]["duration_days"] == 30
    assert items["metformin"]["route"] == "oral"
    assert items["atorvastatin"]["frequency_per_day"] == 1
    assert items["amlodipine"]["frequency_per_day"] == 1


def test_hourly_frequency_is_not_read_as_times_per_day():
    item = parser.parse_line("Tramadol 50mg every 6 hours as needed, maximum 4 doses per day")
    assert item["frequency"].startswith("every 6 hours")
    assert item["frequency_per_day"] == 4
    assert item["prn"] is True
    assert "Maximum 4 doses per day" in item["instructions"]


def test_q8h_notation():
    item = parser.parse_line("Amoxicillin 500mg PO q8h x 7 days")
    assert item["frequency"] == "every 8 hours"
    assert item["frequency_per_day"] == 3


def test_missing_fields_are_flagged_and_not_invented():
    result = parser.parse_prescription("Levothyroxine - take as directed\nOmeprazole 20mg")
    levo = result["items"][0]
    assert levo["strength"] is None and levo["dose"] is None and levo["frequency"] is None
    assert {"strength", "dose", "frequency", "duration"} <= set(levo["needs_verification"])
    assert any("missing" in w for w in result["warnings"])
    omeprazole = result["items"][1]
    assert omeprazole["strength"] == "20mg"
    assert "frequency" in omeprazole["needs_verification"]


def test_empty_and_too_short_input_is_rejected():
    assert parser.parse_prescription("")["errors"]
    assert parser.parse_prescription("   ")["errors"]
    assert parser.parse_prescription("ab")["errors"]


def test_unknown_medication_is_kept_but_reported_as_unrecognised():
    result = parser.parse_prescription("Zyxomycin 250mg twice daily for 5 days")
    item = result["items"][0]
    assert item["medication"].lower().startswith("zyxomycin")
    assert item["recognized"] is False
    assert any("local drug dataset" in w for w in result["warnings"])


def test_routes_are_detected_or_inferred():
    assert parser.parse_line("Salbutamol inhaler, 2 puffs as needed")["route"] == "inhalation"
    assert parser.parse_line("Insulin glargine 10 units subcutaneously at night")["route"] == "subcutaneous"
    assert parser.parse_line("Amoxicillin 500mg capsule three times daily")["route"] == "oral"
    assert parser.parse_line("Nitroglycerin 0.4mg sublingual as needed")["route"] == "sublingual"


def test_duplicate_medication_is_warned_about():
    result = parser.parse_prescription("Ibuprofen 400mg twice daily\nIbuprofen 200mg at night")
    assert any("more than once" in w for w in result["warnings"])


def test_confidence_rises_with_completeness():
    full = parser.parse_line("Amoxicillin 500mg, one capsule three times daily for 5 days")
    sparse = parser.parse_line("Amoxicillin")
    assert full["confidence"] > sparse["confidence"]
    assert full["confidence"] >= 0.9


def test_brand_name_is_normalised_to_the_generic():
    item = parser.parse_line("Brufen 400mg twice daily for 3 days")
    assert item["normalized_name"] == "ibuprofen"
    assert item["display_name"] == "Ibuprofen"


def test_numbered_list_prefixes_are_stripped():
    result = parser.parse_prescription("1. Amoxicillin 500 mg three times a day for 7 days\n"
                                       "2) Prednisolone 10mg once daily for 5 days")
    assert [i["normalized_name"] for i in result["items"]] == ["amoxicillin", "prednisolone"]
