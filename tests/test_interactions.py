"""Requirements 2-5: normalisation, interaction detection, allergy and condition warnings."""
from __future__ import annotations

from backend import interaction_checker, knowledge_graph, medication_normalizer, parser


def _items(text):
    return medication_normalizer.normalize_items(parser.parse_prescription(text)["items"])


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def test_normalisation_maps_brands_and_spellings_to_one_ingredient():
    for name in ("Tylenol", "paracetamol", "Crocin", "acetaminophen"):
        payload = medication_normalizer.normalize(name)
        assert payload["normalized_name"] == "acetaminophen", name
    assert medication_normalizer.normalize("Augmentin")["normalized_name"] == "amoxicillin/clavulanate"


def test_normalisation_carries_an_rxcui_and_a_source():
    payload = medication_normalizer.normalize("amoxicillin")
    assert payload["rxcui"] == "723"
    assert "RxNorm" in payload["source"]
    assert payload["verified_external"] is False  # local dataset, clearly marked


def test_fuzzy_match_handles_a_typo():
    payload = medication_normalizer.normalize("amoxicilin")
    assert payload and payload["normalized_name"] == "amoxicillin"
    assert payload["match_method"] == "fuzzy"


def test_unknown_name_returns_nothing_rather_than_a_guess():
    assert medication_normalizer.normalize("qqzzxx") is None


# --------------------------------------------------------------------------
# Drug-drug interactions
# --------------------------------------------------------------------------
def test_high_concern_pair_is_detected_with_a_source():
    items = _items("Warfarin 5mg once daily\nIbuprofen 400mg three times daily")
    findings, checked, skipped = interaction_checker.check_drug_pairs(items)
    assert len(checked) == 1 and skipped == []
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "high"
    assert finding["source_name"]
    assert "bleeding" in finding["description"].lower()


def test_no_interaction_found_is_reported_as_such():
    items = _items("Cetirizine 10mg once daily\nAcetaminophen 500mg every 6 hours as needed")
    result = interaction_checker.run_full_check(items)
    assert result["interactions"] == []
    assert result["highest_severity"] == "none"
    assert any("not a statement that a combination is safe" in limit.lower()
               for limit in result["limitations"])


def test_three_medications_produce_three_pairs():
    items = _items("Lisinopril 10mg once daily\nSpironolactone 25mg once daily\n"
                   "Potassium chloride 600mg twice daily")
    result = interaction_checker.run_full_check(items)
    assert len(result["checked_pairs"]) == 3
    assert result["counts"]["high"] == 3


def test_duplicate_therapy_is_flagged():
    items = _items("Ibuprofen 400mg twice daily\nNaproxen 250mg twice daily")
    result = interaction_checker.run_full_check(items)
    assert result["interactions"][0]["severity"] == "high"
    assert "nsaid" in result["interactions"][0]["description"].lower() or \
           "two nsaids" in result["interactions"][0]["description"].lower()


def test_same_class_pair_without_a_dataset_entry_is_flagged_as_duplicate_therapy():
    items = _items("Sertraline 50mg once daily\nEscitalopram 10mg once daily")
    findings, _checked, _skipped = interaction_checker.check_drug_pairs(items)
    assert findings and findings[0]["type"] == "duplicate-therapy"
    assert findings[0]["severity"] == "moderate"


def test_unrecognised_medication_is_listed_as_unchecked():
    items = _items("Warfarin 5mg once daily\nZyxomycin 100mg twice daily")
    result = interaction_checker.run_full_check(items)
    assert result["unchecked_medications"]
    assert result["interactions"] == []


# --------------------------------------------------------------------------
# Allergies
# --------------------------------------------------------------------------
def test_direct_allergy_match_is_high_concern():
    items = _items("Amoxicillin 500mg three times daily for 5 days")
    findings = interaction_checker.check_allergies(
        items, [{"substance": "amoxicillin", "reaction": "hives", "severity": "severe"}]
    )
    assert findings[0]["severity"] == "high"
    assert "hives" in findings[0]["description"]


def test_allergy_class_match_catches_a_different_penicillin():
    items = _items("Amoxicillin 500mg three times daily for 5 days")
    findings = interaction_checker.check_allergies(items, [{"substance": "penicillin"}])
    assert findings and findings[0]["severity"] == "high"
    assert "same class" in findings[0]["description"].lower()


def test_cross_reactive_class_is_moderate_not_high():
    items = _items("Cefuroxime 500mg twice daily for 5 days")
    items[0].update({"normalized_name": "cefuroxime", "display_name": "Cefuroxime",
                     "allergy_class": "cephalosporin"})
    findings = interaction_checker.check_allergies(items, [{"substance": "penicillin"}])
    assert findings and findings[0]["severity"] == "moderate"
    assert "cross-react" in findings[0]["description"].lower()


def test_unrelated_allergy_produces_no_warning():
    items = _items("Amoxicillin 500mg three times daily")
    assert interaction_checker.check_allergies(items, [{"substance": "peanut"}]) == []


# --------------------------------------------------------------------------
# Conditions, pregnancy and age
# --------------------------------------------------------------------------
def test_asthma_and_a_non_selective_beta_blocker():
    items = _items("Propranolol 40mg twice daily")
    findings = interaction_checker.check_conditions(items, [{"condition_name": "asthma"}])
    assert any(f["severity"] == "high" for f in findings)


def test_pregnancy_flags_an_ace_inhibitor():
    items = _items("Lisinopril 10mg once daily")
    findings = interaction_checker.check_conditions(items, [], is_pregnant=True)
    assert any(f["severity"] == "high" and "pregnan" in f["condition"] for f in findings)


def test_age_based_nsaid_caution():
    items = _items("Ibuprofen 400mg three times daily")
    assert any(f["condition"] == "age 65 or over"
               for f in interaction_checker.check_conditions(items, [], age=72))
    assert not any(f["condition"] == "age 65 or over"
                   for f in interaction_checker.check_conditions(items, [], age=30))


def test_aspirin_under_18_is_flagged():
    items = _items("Aspirin 300mg twice daily")
    findings = interaction_checker.check_conditions(items, [], age=9)
    assert any("Reye" in f["description"] for f in findings)


# --------------------------------------------------------------------------
# Knowledge graph
# --------------------------------------------------------------------------
def test_graph_contains_the_expected_entities_and_relationships():
    items = _items("Warfarin 5mg once daily\nIbuprofen 400mg three times daily")
    findings = interaction_checker.run_full_check(
        items, allergies=[{"substance": "penicillin"}], conditions=[{"condition_name": "asthma"}]
    )
    graph = knowledge_graph.build_graph(
        {"id": 1, "name": "Test Patient", "age": 70}, items,
        allergies=[{"substance": "penicillin"}], conditions=[{"condition_name": "asthma"}],
        findings=findings, prescription_id=5,
    )
    labels = {node["label"] for node in graph["payload"]["nodes"]}
    rels = {edge["rel"] for edge in graph["payload"]["edges"]}
    assert {"Patient", "Medication", "Prescription", "Condition", "Allergy", "Interaction"} <= labels
    assert {"PATIENT_TAKES", "PRESCRIBED_IN", "PATIENT_ALLERGIC_TO", "PATIENT_HAS_CONDITION",
            "CONTRAINDICATED_WITH"} <= rels
    assert graph["stats"]["medications"] == 2
    assert knowledge_graph.describe_graph(graph["payload"])
    assert "MERGE" in knowledge_graph.cypher_script(graph["payload"])
