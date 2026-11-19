"""Requirements 7 & 10: profiles, schedule, database operations and security."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import pipeline, scheduler
from database import models
from database.db import count_rows_safe, query_one
from utils import security


# --------------------------------------------------------------------------
# Users and security
# --------------------------------------------------------------------------
def test_user_creation_authentication_and_password_storage():
    uid, err = models.create_user("alice", "secret123", "a@example.com", "Alice")
    assert uid and not err
    row = query_one("SELECT * FROM users WHERE id = ?", (uid,))
    assert row["password_hash"] != "secret123"
    assert len(row["password_salt"]) == 32
    assert models.authenticate("alice", "secret123")
    assert models.authenticate("alice", "wrong") is None
    assert models.authenticate("nobody", "secret123") is None


def test_duplicate_username_is_rejected():
    models.create_user("bob", "secret123")
    uid, err = models.create_user("bob", "another123")
    assert uid is None and "already taken" in err


def test_authenticate_never_returns_the_hash():
    models.create_user("carol", "secret123")
    user = models.authenticate("carol", "secret123")
    assert "password_hash" not in user and "password_salt" not in user


def test_password_change_invalidates_the_old_password():
    uid, _ = models.create_user("dave", "secret123")
    models.change_password(uid, "newsecret123")
    assert models.authenticate("dave", "secret123") is None
    assert models.authenticate("dave", "newsecret123")


def test_input_validation_rules():
    assert security.validate_username("ab")[0] is False
    assert security.validate_username("good.user-1")[0] is True
    assert security.validate_password("short")[0] is False
    assert security.validate_password("12345678")[0] is False
    assert security.validate_password("abc12345")[0] is True
    assert security.validate_email("nope")[0] is False
    assert security.validate_email("ok@example.com")[0] is True
    assert "\x07" not in security.sanitize_text("bad\x07text")


def test_log_scrubbing_removes_identifiers():
    scrubbed = security.scrub("patient a@b.com id 123456789")
    assert "a@b.com" not in scrubbed and "123456789" not in scrubbed


# --------------------------------------------------------------------------
# Profiles, allergies, conditions
# --------------------------------------------------------------------------
def test_profile_crud(user_id):
    pid = models.create_profile(user_id, "Asha", "Self", 34, "female", 60.0, "notes")
    assert models.get_profile(pid)["name"] == "Asha"
    models.update_profile(pid, age=35, is_pregnant=True)
    updated = models.get_profile(pid)
    assert updated["age"] == 35 and updated["is_pregnant"] == 1
    assert len(models.list_profiles(user_id)) == 1
    models.delete_profile(pid)
    assert models.get_profile(pid) is None


def test_multiple_profiles_per_user(user_id):
    models.create_profile(user_id, "Self profile")
    models.create_profile(user_id, "Child profile", "Child", 7)
    assert len(models.list_profiles(user_id)) == 2


def test_allergy_and_condition_crud(patient):
    aid = models.add_allergy(patient["id"], "Penicillin", "rash", "moderate")
    assert models.list_allergies(patient["id"])[0]["substance"] == "penicillin"
    cid = models.add_condition(patient["id"], "Asthma")
    assert models.list_conditions(patient["id"])[0]["condition_name"] == "asthma"
    models.delete_allergy(aid)
    models.delete_condition(cid)
    assert models.list_allergies(patient["id"]) == []
    assert models.list_conditions(patient["id"]) == []


def test_deleting_a_profile_cascades(patient):
    outcome = pipeline.analyze("Amoxicillin 500mg three times daily for 5 days", patient,
                               save=True, build_schedule=True)
    rx_id = outcome["prescription_id"]
    assert rx_id
    models.delete_profile(patient["id"])
    assert models.get_prescription(rx_id) is None
    assert models.list_schedule(patient["id"]) == []


# --------------------------------------------------------------------------
# Prescriptions and reports
# --------------------------------------------------------------------------
def test_prescription_round_trip(patient):
    outcome = pipeline.analyze(
        "Warfarin 5mg once daily at night\nIbuprofen 400mg three times daily for 7 days",
        patient, save=True,
    )
    rx = models.get_prescription(outcome["prescription_id"])
    assert rx["patient_id"] == patient["id"]
    meds = models.list_prescription_medications(rx["id"])
    assert {m["normalized_name"] for m in meds} == {"warfarin", "ibuprofen"}
    assert outcome["report_id"]
    stored = models.get_safety_report(outcome["report_id"])
    assert stored["highest_severity"] == "high"
    assert stored["report"]["interactions"][0]["source_name"]


def test_status_transitions_and_current_medications(patient):
    outcome = pipeline.analyze("Amoxicillin 500mg three times daily for 5 days", patient, save=True)
    assert len(models.current_medications(patient["id"])) == 1
    models.set_prescription_status(outcome["prescription_id"], "completed")
    assert models.current_medications(patient["id"]) == []
    assert len(models.list_prescriptions(patient["id"], status="completed")) == 1


def test_chat_history_round_trip(user_id, patient):
    models.save_chat(user_id, patient["id"], "user", "What is this for?")
    models.save_chat(user_id, patient["id"], "assistant", "See the sources.",
                     sources=[{"title": "doc", "source_name": "FDA"}])
    history = models.list_chat(user_id, patient["id"])
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["sources"][0]["source_name"] == "FDA"
    models.clear_chat(user_id, patient["id"])
    assert models.list_chat(user_id, patient["id"]) == []


def test_interaction_dataset_is_seeded():
    assert models.count_interactions() > 40
    assert models.find_interaction("ibuprofen", "warfarin")["severity"] == "high"
    assert models.find_interaction("warfarin", "ibuprofen")["severity"] == "high"
    assert models.find_interaction("cetirizine", "acetaminophen") is None
    assert count_rows_safe()["drug_interactions"] > 40


# --------------------------------------------------------------------------
# Schedule and adherence
# --------------------------------------------------------------------------
def test_schedule_is_generated_from_the_written_frequency(patient):
    outcome = pipeline.analyze("Amoxicillin 500mg, one capsule three times daily for 5 days",
                               patient, save=True, build_schedule=True)
    created = outcome["schedule"]["created"]
    assert len(created) == 1
    entry = created[0]
    assert entry["doses_per_day"] == 3
    assert entry["end_date"] == (date.today() + timedelta(days=4)).isoformat()
    plan = scheduler.day_plan(patient["id"])
    assert len(plan) == 3
    assert [s["dose_index"] for s in plan] == [1, 2, 3]
    assert plan[0]["timing_specified"] is False  # no clock times invented


def test_no_schedule_when_frequency_is_missing(patient):
    outcome = pipeline.analyze("Levothyroxine - take as directed", patient,
                               save=True, build_schedule=True)
    assert outcome["schedule"]["created"] == []
    assert "does not state how many doses per day" in outcome["schedule"]["skipped"][0]["reason"]


def test_marking_doses_updates_adherence(patient):
    outcome = pipeline.analyze("Amoxicillin 500mg, one capsule three times daily for 5 days",
                               patient, save=True, build_schedule=True)
    sched_id = outcome["schedule"]["created"][0]["schedule_id"]
    today = date.today().isoformat()
    scheduler.mark_dose(patient["id"], sched_id, today, 1, "taken")
    scheduler.mark_dose(patient["id"], sched_id, today, 2, "missed")
    summary = scheduler.adherence_summary(patient["id"], days=1)
    assert summary["scheduled"] == 3 and summary["taken"] == 1 and summary["missed"] == 1
    assert summary["adherence_percent"] == pytest.approx(33.3, abs=0.2)
    # Re-marking the same slot updates rather than duplicating
    scheduler.mark_dose(patient["id"], sched_id, today, 2, "taken")
    assert scheduler.adherence_summary(patient["id"], days=1)["taken"] == 2


def test_invalid_dose_status_is_rejected(patient):
    with pytest.raises(ValueError):
        scheduler.mark_dose(patient["id"], 1, date.today().isoformat(), 1, "whatever")


def test_open_ended_course_has_no_end_date(patient):
    outcome = pipeline.analyze("Amlodipine 5mg once daily", patient, save=True, build_schedule=True)
    entry = outcome["schedule"]["created"][0]
    assert entry["open_ended"] is True
    assert scheduler.course_status(models.get_schedule_entry(entry["schedule_id"])) == "active"


def test_finished_course_is_marked_completed(patient):
    rx_id = models.save_prescription(patient["id"], "Amoxicillin 500mg", [])
    models.create_schedule_entry(
        patient["id"], rx_id, None, "Amoxicillin", "1 capsule", "3 times daily", 3,
        (date.today() - timedelta(days=10)).isoformat(),
        (date.today() - timedelta(days=5)).isoformat(),
    )
    assert scheduler.complete_finished_courses(patient["id"]) == 1
    assert models.get_prescription(rx_id)["status"] == "completed"


def test_dashboard_metrics(patient):
    pipeline.analyze("Warfarin 5mg once daily\nIbuprofen 400mg three times daily for 7 days",
                     patient, save=True, build_schedule=True)
    metrics = pipeline.dashboard_metrics(patient["id"])
    assert metrics["total_prescriptions"] == 1
    assert metrics["current_medications"] == 2
    assert metrics["interaction_alerts"] == 1
    assert metrics["high_alerts"] == 1
    assert metrics["upcoming_doses"] > 0
