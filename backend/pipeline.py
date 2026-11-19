"""End-to-end analysis pipeline.

User text -> parser -> normalisation -> knowledge graph -> interaction/allergy/
condition checks -> safety report -> (optionally) persisted records + schedule.

Keeping the orchestration here means the Streamlit pages contain only UI code.
"""
from __future__ import annotations

from backend import interaction_checker, knowledge_graph, medication_normalizer, parser, safety_report, scheduler
from database import models
from database.db import ensure_db
from utils.security import log_error, log_info


def analyze(text: str, patient, *, save: bool = False, build_schedule: bool = False,
            prescriber: str = "", source: str = "typed", ocr_engine=None,
            ocr_confidence=None, ocr_verified: bool = False) -> dict:
    """Run the full pipeline. Returns a dict with every intermediate result."""
    ensure_db()
    outcome = {
        "ok": False,
        "parse": None,
        "items": [],
        "findings": None,
        "graph": None,
        "report": None,
        "prescription_id": None,
        "report_id": None,
        "schedule": None,
        "errors": [],
        "warnings": [],
    }

    parse_result = parser.parse_prescription(text)
    outcome["parse"] = parse_result
    outcome["errors"].extend(parse_result["errors"])
    outcome["warnings"].extend(parse_result["warnings"])
    if not parse_result["items"]:
        return outcome

    items = medication_normalizer.normalize_items(parse_result["items"])
    outcome["items"] = items

    patient_id = (patient or {}).get("id")
    allergies = models.list_allergies(patient_id) if patient_id else []
    conditions = models.list_conditions(patient_id) if patient_id else []

    findings = interaction_checker.run_full_check(
        items,
        allergies=allergies,
        conditions=conditions,
        is_pregnant=bool((patient or {}).get("is_pregnant")),
        age=(patient or {}).get("age"),
        weight_kg=(patient or {}).get("weight_kg"),
    )
    outcome["findings"] = findings

    prescription_id = None
    if save and patient_id:
        try:
            prescription_id = models.save_prescription(
                patient_id, text, items, prescriber=prescriber, source=source,
                ocr_engine=ocr_engine, ocr_confidence=ocr_confidence,
                ocr_verified=ocr_verified,
            )
            outcome["prescription_id"] = prescription_id
        except Exception as exc:  # pragma: no cover - defensive
            log_error("saving prescription", exc)
            outcome["warnings"].append("The prescription could not be saved to the database.")

    graph = knowledge_graph.build_graph(
        patient or {}, items, allergies=allergies, conditions=conditions,
        findings=findings, prescription_id=prescription_id,
    )
    outcome["graph"] = graph

    report = safety_report.build_report(
        patient=patient or {},
        items=items,
        findings=findings,
        raw_text=text,
        prescription_id=prescription_id,
        allergies=allergies,
        conditions=conditions,
        parser_info=parse_result["parser"],
        graph_info={"backend": graph["backend"], **graph["stats"]},
    )
    outcome["report"] = report

    if save and patient_id:
        try:
            outcome["report_id"] = models.save_safety_report(patient_id, prescription_id, report)
        except Exception as exc:  # pragma: no cover
            log_error("saving safety report", exc)

    if build_schedule and prescription_id:
        med_rows = models.list_prescription_medications(prescription_id)
        outcome["schedule"] = scheduler.create_schedule_for_prescription(
            patient_id, prescription_id, med_rows
        )

    outcome["ok"] = True
    log_info("Analysis complete: %d medication(s), highest severity %s"
             % (len(items), findings["highest_severity"]))
    return outcome


def rag_context(patient_id: int, *, items=None, report=None, graph_payload=None) -> dict:
    """Build the retrieval context for the chatbot from stored + live data."""
    ensure_db()
    context = {"items": list(items or []), "report": report or {}}

    if not context["items"] and patient_id:
        rows = models.current_medications(patient_id)
        context["items"] = [
            {
                "medication": r.get("medication_text"),
                "display_name": r.get("medication_text"),
                "normalized_name": r.get("normalized_name"),
                "rxcui": r.get("rxcui"),
                "strength": r.get("strength"),
                "dose": r.get("dose"),
                "frequency": r.get("frequency"),
                "duration": r.get("duration"),
                "route": r.get("route"),
                "instructions": r.get("instructions"),
                "normalization_source": "MediGraph AI database (saved prescription)",
            }
            for r in rows
        ]
        for item in context["items"]:
            info = medication_normalizer.medication_info(item.get("normalized_name") or "")
            if info:
                item["drug_class"] = info.get("drug_class")
                item["common_uses"] = info.get("common_uses")

    if not context["report"] and patient_id:
        reports = models.list_safety_reports(patient_id, limit=1)
        if reports:
            stored = models.get_safety_report(reports[0]["id"])
            context["report"] = (stored or {}).get("report", {})

    if graph_payload:
        context["graph_facts"] = knowledge_graph.describe_graph(graph_payload)

    if patient_id:
        context["history_summary"] = history_summary(patient_id)
    return context


def history_summary(patient_id: int) -> str:
    """One-paragraph summary of this patient's medication history for retrieval."""
    prescriptions = models.list_prescriptions(patient_id)
    if not prescriptions:
        return "No prescriptions have been saved for this patient profile yet."
    parts = []
    for rx in prescriptions[:12]:
        meds = models.list_prescription_medications(rx["id"])
        names = ", ".join(filter(None, (m.get("medication_text") for m in meds))) or "no medications parsed"
        parts.append("Prescription #%s (%s, status %s): %s."
                     % (rx["id"], str(rx["created_at"])[:10], rx["status"], names))
    adherence = scheduler.adherence_summary(patient_id, days=14)
    parts.append(
        "Over the last 14 days %d doses were scheduled, %d marked taken and %d marked missed (%s%% adherence)."
        % (adherence["scheduled"], adherence["taken"], adherence["missed"],
           adherence["adherence_percent"])
    )
    return " ".join(parts)


def dashboard_metrics(patient_id: int) -> dict:
    ensure_db()
    prescriptions = models.list_prescriptions(patient_id)
    current = models.current_medications(patient_id)
    reports = models.list_safety_reports(patient_id, limit=200)
    alerts = sum(1 for r in reports if r.get("highest_severity") in ("high", "moderate"))
    high = sum(1 for r in reports if r.get("highest_severity") == "high")
    upcoming = scheduler.upcoming_doses(patient_id, days=2)
    adherence = scheduler.adherence_summary(patient_id, days=7)
    return {
        "total_prescriptions": len(prescriptions),
        "active_prescriptions": sum(1 for p in prescriptions if p["status"] == "active"),
        "current_medications": len({(m.get("normalized_name") or m.get("medication_text")) for m in current}),
        "interaction_alerts": alerts,
        "high_alerts": high,
        "upcoming_doses": len(upcoming),
        "upcoming": upcoming[:8],
        "adherence": adherence,
        "recent_prescriptions": prescriptions[:5],
        "recent_reports": reports[:5],
    }
