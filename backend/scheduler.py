"""Dosage schedule and adherence tracking.

Rule: a schedule is generated only from instructions the prescription actually
contains. When the number of doses per day is not stated, no doses are
generated and the entry is marked for verification. Clock times are never
invented - the schedule shows "dose 1 of 3" rather than a made-up 08:00.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from database import models
from utils.helpers import parse_date, safe_percent, today_iso

_EXPLICIT_TIMING_RE = re.compile(
    r"\b(?:every\s*\d+\s*hours?|morning|night|bedtime|noon|midday|breakfast|lunch|dinner|"
    r"\d{1,2}\s*(?::\d{2})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)


def timing_is_specified(frequency_text) -> bool:
    return bool(_EXPLICIT_TIMING_RE.search(str(frequency_text or "")))


def create_schedule_for_prescription(patient_id: int, prescription_id: int, med_rows) -> dict:
    """Create schedule entries for each parsed medication row.

    `med_rows` are rows from `prescription_medications` (or equivalent dicts).
    Returns {"created": [...], "skipped": [...]}.
    """
    created, skipped = [], []
    rx = models.get_prescription(prescription_id)
    start = parse_date((rx or {}).get("start_date")) or date.today()
    for row in med_rows:
        name = row.get("medication_text") or row.get("normalized_name") or "Unknown medication"
        per_day = row.get("frequency_per_day")
        duration_days = row.get("duration_days")
        if not per_day:
            skipped.append({
                "medication": name,
                "reason": "The prescription does not state how many doses per day, so no "
                          "schedule was generated. Confirm the frequency and add it manually.",
            })
            continue
        end = start + timedelta(days=int(duration_days) - 1) if duration_days else None
        sched_id = models.create_schedule_entry(
            patient_id=patient_id,
            prescription_id=prescription_id,
            prescription_med_id=row.get("id"),
            medication_name=name,
            dose=row.get("dose"),
            frequency_text=row.get("frequency"),
            doses_per_day=per_day,
            start_date=start.isoformat(),
            end_date=end.isoformat() if end else None,
            timing_specified=timing_is_specified(row.get("frequency")),
        )
        created.append({
            "schedule_id": sched_id,
            "medication": name,
            "doses_per_day": per_day,
            "start_date": start.isoformat(),
            "end_date": end.isoformat() if end else None,
            "open_ended": end is None,
        })
    return {"created": created, "skipped": skipped}


def _doses_for_day(entry, day: date):
    """Dose slots for one schedule entry on one day (never invents clock times)."""
    start = parse_date(entry.get("start_date"))
    end = parse_date(entry.get("end_date"))
    if not start or day < start or (end and day > end):
        return []
    per_day = entry.get("doses_per_day") or 0
    if per_day <= 0:
        return []
    if per_day < 1:  # weekly / alternate-day dosing
        interval = round(1 / per_day)
        if (day - start).days % interval != 0:
            return []
        count = 1
    else:
        count = int(round(per_day))
    label = entry.get("frequency_text") or ""
    return [
        {
            "schedule_id": entry["id"],
            "medication_name": entry["medication_name"],
            "dose": entry.get("dose"),
            "dose_index": i + 1,
            "dose_label": "Dose %d of %d" % (i + 1, count),
            "frequency_text": label,
            "date": day.isoformat(),
            "timing_specified": bool(entry.get("timing_specified")),
        }
        for i in range(count)
    ]


def day_plan(patient_id: int, day=None) -> list[dict]:
    """All dose slots for a patient on one day, with their recorded status."""
    day = day or date.today()
    if isinstance(day, str):
        day = parse_date(day) or date.today()
    entries = models.list_schedule(patient_id)
    records = {
        (r["schedule_id"], r["dose_date"], r["dose_index"]): r["status"]
        for r in models.list_adherence(patient_id, day.isoformat(), day.isoformat())
    }
    plan = []
    for entry in entries:
        for slot in _doses_for_day(entry, day):
            slot["status"] = records.get(
                (slot["schedule_id"], slot["date"], slot["dose_index"]), "pending"
            )
            plan.append(slot)
    plan.sort(key=lambda s: (s["medication_name"], s["dose_index"]))
    return plan


def upcoming_doses(patient_id: int, days: int = 3) -> list[dict]:
    out = []
    for offset in range(max(days, 1)):
        day = date.today() + timedelta(days=offset)
        for slot in day_plan(patient_id, day):
            if offset == 0 and slot["status"] != "pending":
                continue
            out.append(slot)
    return out


def mark_dose(patient_id: int, schedule_id: int, dose_date: str, dose_index: int, status: str):
    if status not in ("taken", "missed", "pending"):
        raise ValueError("status must be taken, missed or pending")
    return models.record_dose(schedule_id, patient_id, dose_date, dose_index, status)


def adherence_history(patient_id: int, days: int = 7) -> list[dict]:
    """Per-day adherence for the last `days` days (today last)."""
    history = []
    for offset in range(max(days, 1) - 1, -1, -1):
        day = date.today() - timedelta(days=offset)
        plan = day_plan(patient_id, day)
        taken = sum(1 for s in plan if s["status"] == "taken")
        missed = sum(1 for s in plan if s["status"] == "missed")
        pending = sum(1 for s in plan if s["status"] == "pending")
        history.append({
            "date": day.isoformat(),
            "scheduled": len(plan),
            "taken": taken,
            "missed": missed,
            "pending": pending,
            "adherence_percent": safe_percent(taken, len(plan)),
        })
    return history


def adherence_summary(patient_id: int, days: int = 7) -> dict:
    history = adherence_history(patient_id, days)
    scheduled = sum(d["scheduled"] for d in history)
    taken = sum(d["taken"] for d in history)
    missed = sum(d["missed"] for d in history)
    return {
        "days": days,
        "scheduled": scheduled,
        "taken": taken,
        "missed": missed,
        "pending": scheduled - taken - missed,
        "adherence_percent": safe_percent(taken, scheduled),
        "history": history,
    }


def reminder_lines(patient_id: int) -> list[str]:
    """Plain-text reminders for entries with reminders enabled."""
    lines = []
    for entry in models.list_schedule(patient_id):
        if not entry.get("reminder_enabled"):
            continue
        slots = _doses_for_day(entry, date.today())
        if not slots:
            continue
        timing = (
            entry.get("frequency_text") or "as prescribed"
            if entry.get("timing_specified")
            else "%s (exact times not specified in the prescription - "
                 "use times agreed with your pharmacist)" % (entry.get("frequency_text") or "")
        )
        lines.append(
            "%s - %s dose(s) today: %s. %s"
            % (entry["medication_name"], len(slots), entry.get("dose") or "dose as prescribed", timing)
        )
    return lines


def timing_guidance_for_patient(patient_id: int) -> list[dict]:
    """Food / alcohol / spacing rules for the medications currently scheduled."""
    from backend import dose_checker, medication_normalizer

    seen, items = set(), []
    for entry in list_schedule_active(patient_id):
        name = entry.get("medication_name") or ""
        payload = medication_normalizer.normalize_local(name)
        key = (payload or {}).get("normalized_name") or name.lower()
        if not payload or key in seen:
            continue
        seen.add(key)
        items.append({
            "medication": name,
            "display_name": payload.get("display_name") or name,
            "normalized_name": key,
            "ingredients": payload.get("ingredients", []),
            "allergy_class": payload.get("allergy_class"),
            "drug_class": payload.get("drug_class"),
        })
    return dose_checker.timing_guidance(items)


def list_schedule_active(patient_id: int) -> list[dict]:
    return [e for e in models.list_schedule(patient_id) if course_status(e) == "active"]


def course_status(entry) -> str:
    end = parse_date(entry.get("end_date"))
    start = parse_date(entry.get("start_date"))
    today = date.today()
    if start and today < start:
        return "not started"
    if end and today > end:
        return "completed"
    return "active"


def complete_finished_courses(patient_id: int) -> int:
    """Mark prescriptions whose last schedule entry has ended as completed."""
    changed = 0
    for rx in models.list_prescriptions(patient_id, status="active"):
        entries = [e for e in models.list_schedule(patient_id)
                   if e["prescription_id"] == rx["id"]]
        if not entries:
            continue
        if all(course_status(e) == "completed" for e in entries):
            models.set_prescription_status(rx["id"], "completed")
            changed += 1
    return changed


def today_str() -> str:
    return today_iso()
