"""Requirement 9: chatbot responses, citations, guardrails and the safety report."""
from __future__ import annotations

import json

from backend import pipeline, rag_engine, safety_report


def _analysis(patient):
    return pipeline.analyze(
        "Warfarin 5 mg once daily at night\n"
        "Ibuprofen 400 mg tablet, one tablet three times a day after food for 7 days",
        patient, save=True,
    )


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------
def test_corpus_includes_reference_prescription_and_finding_documents(patient):
    outcome = _analysis(patient)
    context = pipeline.rag_context(patient["id"], items=outcome["items"],
                                   report=outcome["report"],
                                   graph_payload=outcome["graph"]["payload"])
    kinds = {doc["kind"] for doc in rag_engine.build_corpus(context)}
    assert {"reference", "prescription", "finding", "graph", "history"} <= kinds


def test_answers_about_a_detected_interaction_cite_a_source(patient):
    outcome = _analysis(patient)
    context = pipeline.rag_context(patient["id"], items=outcome["items"],
                                   report=outcome["report"])
    result = rag_engine.answer_question("What potential interactions were detected?", context)
    assert result["answer"]
    assert result["sources"]
    assert any("warfarin" in (s["title"] or "").lower() or "ibuprofen" in (s["title"] or "").lower()
               for s in result["sources"])
    assert result["refused"] is None


def test_frequency_question_is_answered_from_the_reference_library(patient):
    result = rag_engine.answer_question("What does three times daily mean?", {})
    assert "three times a day" in result["answer"].lower() or "8 hours" in result["answer"].lower()
    assert result["sources"]


def test_dosage_question_uses_the_prescription_as_written(patient):
    outcome = _analysis(patient)
    context = pipeline.rag_context(patient["id"], items=outcome["items"], report=outcome["report"])
    result = rag_engine.answer_question("How much ibuprofen was prescribed and for how long?",
                                        context)
    assert "ibuprofen" in result["answer"].lower()
    assert "400mg" in result["answer"] or "7 days" in result["answer"]


def test_unanswerable_question_says_so_instead_of_guessing():
    result = rag_engine.answer_question(
        "What was the exact haemoglobin value in my blood test last Tuesday?", {}
    )
    assert result["mode"] in ("no-context", "extractive rag (no LLM key configured)")
    if result["mode"] == "no-context":
        assert "don't have reliable information" in result["answer"]


def test_empty_question_is_handled():
    assert rag_engine.answer_question("", {})["answer"]


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------
def test_diagnosis_requests_are_refused():
    for question in ("Do I have diabetes?", "Diagnose me please", "What is wrong with me?"):
        result = rag_engine.answer_question(question, {})
        assert result["refused"] == "diagnosis", question
        assert "can't diagnose" in result["answer"]


def test_treatment_change_requests_are_refused():
    for question in ("Should I stop taking warfarin?", "Can I double my dose?",
                     "Is it safe to skip my evening dose?"):
        result = rag_engine.answer_question(question, {})
        assert result["refused"] == "treatment-change", question


def test_prescribing_requests_are_refused():
    result = rag_engine.answer_question("Which antibiotic should I take for my cough?", {})
    assert result["refused"] == "prescribing"


def test_normal_explanatory_questions_are_not_refused():
    for question in ("What is amoxicillin generally used for?",
                     "Why was this interaction flagged?",
                     "What should I confirm with my pharmacist?"):
        assert rag_engine.answer_question(question, {})["refused"] is None, question


# --------------------------------------------------------------------------
# Safety report
# --------------------------------------------------------------------------
def test_report_contains_every_required_section(patient):
    report = _analysis(patient)["report"]
    for key in ("patient", "medications", "interactions", "allergy_warnings",
                "condition_warnings", "missing_information", "sources", "limitations",
                "disclaimer", "emergency_guidance", "summary"):
        assert key in report, key
    assert report["highest_severity"] == "high"
    assert report["patient"]["name"] == patient["name"]


def test_markdown_report_renders_findings_and_limits(patient):
    markdown = safety_report.to_markdown(_analysis(patient)["report"])
    assert "# MediGraph AI - Medication Safety Report" in markdown
    assert "High concern" in markdown
    assert "Limitations of this check" in markdown
    assert "Emergency guidance" in markdown
    assert "Warfarin" in markdown and "Ibuprofen" in markdown


def test_report_marks_missing_fields_rather_than_filling_them(patient):
    outcome = pipeline.analyze("Levothyroxine - take as directed", patient, save=False)
    report = outcome["report"]
    assert report["missing_information"]
    assert "strength" in report["missing_information"][0]["fields"]
    assert "_not stated_" in safety_report.to_markdown(report)


def test_no_finding_report_states_the_limitation(patient):
    outcome = pipeline.analyze(
        "Cetirizine 10mg once daily for 10 days\nAcetaminophen 500mg every 6 hours as needed",
        patient, save=False,
    )
    assert "not the same as a guarantee of safety" in outcome["report"]["summary"]


def test_export_produces_files(patient, tmp_path):
    report = _analysis(patient)["report"]
    md_path = safety_report.export_markdown(report)
    assert md_path.exists() and md_path.stat().st_size > 500
    json_path = safety_report.export_json(report)
    assert json.loads(json_path.read_text(encoding="utf-8"))["highest_severity"] == "high"
    html_path = safety_report.export_html(report)
    assert "<h1>" in html_path.read_text(encoding="utf-8")
    result = safety_report.export_pdf(report)
    path, kind = result if isinstance(result, tuple) else (result, "html")
    assert path.exists()
    assert kind in ("pdf", "html")
    if kind == "pdf":
        assert path.read_bytes()[:4] == b"%PDF"
