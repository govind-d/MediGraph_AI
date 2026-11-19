"""Data-access layer: every table gets small, explicit CRUD functions."""
from __future__ import annotations

import json

from database.db import execute, query, query_one
from utils.helpers import now_iso, today_iso
from utils.security import hash_password, verify_password

# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def create_user(username: str, password: str, email: str = "", full_name: str = ""):
    """Returns (user_id, error_message)."""
    username = username.strip()
    if get_user_by_username(username):
        return None, "That username is already taken."
    pw_hash, salt = hash_password(password)
    uid = execute(
        """INSERT INTO users (username, email, password_hash, password_salt, full_name, created_at)
           VALUES (?,?,?,?,?,?)""",
        (username, email.strip() or None, pw_hash, salt, full_name.strip() or None, now_iso()),
    )
    return uid, ""


def get_user_by_username(username: str):
    return query_one("SELECT * FROM users WHERE username = ?", (username.strip(),))


def get_user(user_id: int):
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def authenticate(username: str, password: str):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"], user["password_salt"]):
        return None
    execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user["id"]))
    return {k: v for k, v in user.items() if k not in ("password_hash", "password_salt")}


def change_password(user_id: int, new_password: str) -> None:
    pw_hash, salt = hash_password(new_password)
    execute(
        "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
        (pw_hash, salt, user_id),
    )


def delete_user(user_id: int) -> None:
    execute("DELETE FROM users WHERE id = ?", (user_id,))


# --------------------------------------------------------------------------
# Patient profiles
# --------------------------------------------------------------------------


def create_profile(user_id: int, name: str, relationship="Self", age=None, sex=None,
                   weight_kg=None, notes="", is_pregnant=False):
    return execute(
        """INSERT INTO patient_profiles
           (user_id, name, relationship, age, sex, weight_kg, notes, is_pregnant, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user_id, name.strip(), relationship, age, sex, weight_kg, notes.strip() or None,
         1 if is_pregnant else 0, now_iso(), now_iso()),
    )


def update_profile(profile_id: int, **fields) -> None:
    allowed = {"name", "relationship", "age", "sex", "weight_kg", "notes", "is_pregnant"}
    sets, params = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append("%s = ?" % key)
            params.append(int(value) if key == "is_pregnant" else value)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.extend([now_iso(), profile_id])
    execute("UPDATE patient_profiles SET %s WHERE id = ?" % ", ".join(sets), tuple(params))


def delete_profile(profile_id: int) -> None:
    execute("DELETE FROM patient_profiles WHERE id = ?", (profile_id,))


def list_profiles(user_id: int):
    return query("SELECT * FROM patient_profiles WHERE user_id = ? ORDER BY id", (user_id,))


def get_profile(profile_id: int):
    return query_one("SELECT * FROM patient_profiles WHERE id = ?", (profile_id,))


# --------------------------------------------------------------------------
# Allergies and conditions
# --------------------------------------------------------------------------


def add_allergy(patient_id: int, substance: str, reaction="", severity="unknown"):
    return execute(
        """INSERT INTO patient_allergies (patient_id, substance, reaction, severity, recorded_at)
           VALUES (?,?,?,?,?)""",
        (patient_id, substance.strip().lower(), reaction.strip() or None, severity, now_iso()),
    )


def list_allergies(patient_id: int):
    return query("SELECT * FROM patient_allergies WHERE patient_id = ? ORDER BY id", (patient_id,))


def delete_allergy(allergy_id: int) -> None:
    execute("DELETE FROM patient_allergies WHERE id = ?", (allergy_id,))


def add_condition(patient_id: int, condition_name: str, status="active", diagnosed_on=None):
    return execute(
        """INSERT INTO medical_conditions (patient_id, condition_name, status, diagnosed_on, recorded_at)
           VALUES (?,?,?,?,?)""",
        (patient_id, condition_name.strip().lower(), status, diagnosed_on, now_iso()),
    )


def list_conditions(patient_id: int):
    return query("SELECT * FROM medical_conditions WHERE patient_id = ? ORDER BY id", (patient_id,))


def delete_condition(condition_id: int) -> None:
    execute("DELETE FROM medical_conditions WHERE id = ?", (condition_id,))


# --------------------------------------------------------------------------
# Medication catalogue
# --------------------------------------------------------------------------


def upsert_medication(normalized_name: str, display_name: str, rxcui=None, drug_class=None,
                      ingredients=None, common_uses=None, source="local"):
    normalized_name = (normalized_name or "").lower()
    existing = query_one("SELECT * FROM medications WHERE normalized_name = ?", (normalized_name,))
    if existing:
        return existing["id"]
    return execute(
        """INSERT INTO medications
           (normalized_name, display_name, rxcui, drug_class, ingredients, common_uses, source, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (normalized_name, display_name, rxcui, drug_class, json.dumps(ingredients or []),
         common_uses, source, now_iso()),
    )


def get_medication(normalized_name: str):
    return query_one(
        "SELECT * FROM medications WHERE normalized_name = ?", ((normalized_name or "").lower(),)
    )


def all_medications():
    return query("SELECT * FROM medications ORDER BY display_name")


# --------------------------------------------------------------------------
# Prescriptions
# --------------------------------------------------------------------------


def save_prescription(patient_id: int, raw_text: str, parsed_items, prescriber="",
                      start_date=None, source="typed", ocr_engine=None, ocr_confidence=None,
                      ocr_verified=False):
    rx_id = execute(
        """INSERT INTO prescriptions
           (patient_id, raw_text, parsed_json, prescriber, status, start_date, source,
            ocr_engine, ocr_confidence, ocr_verified, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (patient_id, raw_text, json.dumps(parsed_items), prescriber.strip() or None,
         "active", start_date or today_iso(), source, ocr_engine, ocr_confidence,
         1 if ocr_verified else 0, now_iso()),
    )
    for item in parsed_items:
        med_id = None
        if item.get("normalized_name"):
            med_id = upsert_medication(
                item["normalized_name"],
                item.get("display_name") or item.get("medication") or item["normalized_name"],
                rxcui=item.get("rxcui"),
                drug_class=item.get("drug_class"),
                ingredients=item.get("ingredients"),
                common_uses=item.get("common_uses"),
                source=item.get("normalization_source", "local"),
            )
        execute(
            """INSERT INTO prescription_medications
               (prescription_id, medication_id, medication_text, normalized_name, rxcui, strength,
                dose, frequency, frequency_per_day, duration, duration_days, route, instructions,
                needs_verification, confidence, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rx_id, med_id, item.get("medication") or "", item.get("normalized_name"),
             item.get("rxcui"), item.get("strength"), item.get("dose"), item.get("frequency"),
             item.get("frequency_per_day"), item.get("duration"), item.get("duration_days"),
             item.get("route"), item.get("instructions"),
             json.dumps(item.get("needs_verification", [])), item.get("confidence"), now_iso()),
        )
    return rx_id


def list_prescriptions(patient_id: int, status=None):
    if status:
        return query(
            "SELECT * FROM prescriptions WHERE patient_id = ? AND status = ? ORDER BY created_at DESC",
            (patient_id, status),
        )
    return query(
        "SELECT * FROM prescriptions WHERE patient_id = ? ORDER BY created_at DESC", (patient_id,)
    )


def get_prescription(rx_id: int):
    return query_one("SELECT * FROM prescriptions WHERE id = ?", (rx_id,))


def set_prescription_status(rx_id: int, status: str) -> None:
    execute("UPDATE prescriptions SET status = ? WHERE id = ?", (status, rx_id))


def delete_prescription(rx_id: int) -> None:
    execute("DELETE FROM prescriptions WHERE id = ?", (rx_id,))


def list_prescription_medications(rx_id: int):
    return query(
        "SELECT * FROM prescription_medications WHERE prescription_id = ? ORDER BY id", (rx_id,)
    )


def current_medications(patient_id: int):
    """Parsed medication rows belonging to active prescriptions."""
    return query(
        """SELECT pm.*, p.created_at AS rx_created_at, p.id AS rx_id
           FROM prescription_medications pm
           JOIN prescriptions p ON p.id = pm.prescription_id
           WHERE p.patient_id = ? AND p.status = 'active'
           ORDER BY p.created_at DESC, pm.id""",
        (patient_id,),
    )


# --------------------------------------------------------------------------
# Interactions knowledge base
# --------------------------------------------------------------------------


def find_interaction(drug_a: str, drug_b: str):
    a, b = (drug_a or "").lower(), (drug_b or "").lower()
    return query_one(
        """SELECT * FROM drug_interactions
           WHERE (drug_a = ? AND drug_b = ?) OR (drug_a = ? AND drug_b = ?)""",
        (a, b, b, a),
    )


def interactions_for(drug: str):
    d = (drug or "").lower()
    return query("SELECT * FROM drug_interactions WHERE drug_a = ? OR drug_b = ?", (d, d))


def count_interactions() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM drug_interactions")
    return row["n"] if row else 0


# --------------------------------------------------------------------------
# Safety reports
# --------------------------------------------------------------------------


def save_safety_report(patient_id: int, prescription_id, report: dict):
    return execute(
        """INSERT INTO safety_reports
           (patient_id, prescription_id, report_json, highest_severity, finding_count, created_at)
           VALUES (?,?,?,?,?,?)""",
        (patient_id, prescription_id, json.dumps(report), report.get("highest_severity", "none"),
         len(report.get("interactions", [])) + len(report.get("allergy_warnings", []))
         + len(report.get("condition_warnings", [])), now_iso()),
    )


def list_safety_reports(patient_id: int, limit: int = 50):
    return query(
        "SELECT * FROM safety_reports WHERE patient_id = ? ORDER BY created_at DESC LIMIT ?",
        (patient_id, limit),
    )


def get_safety_report(report_id: int):
    row = query_one("SELECT * FROM safety_reports WHERE id = ?", (report_id,))
    if row:
        row["report"] = json.loads(row["report_json"])
    return row


# --------------------------------------------------------------------------
# Schedule and adherence
# --------------------------------------------------------------------------


def create_schedule_entry(patient_id, prescription_id, prescription_med_id, medication_name,
                          dose, frequency_text, doses_per_day, start_date, end_date,
                          timing_specified=False, reminder_enabled=False):
    return execute(
        """INSERT INTO medication_schedule
           (patient_id, prescription_id, prescription_med_id, medication_name, dose, frequency_text,
            doses_per_day, start_date, end_date, timing_specified, reminder_enabled, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (patient_id, prescription_id, prescription_med_id, medication_name, dose, frequency_text,
         doses_per_day, start_date, end_date, 1 if timing_specified else 0,
         1 if reminder_enabled else 0, now_iso()),
    )


def list_schedule(patient_id: int):
    return query(
        "SELECT * FROM medication_schedule WHERE patient_id = ? ORDER BY start_date DESC, id",
        (patient_id,),
    )


def get_schedule_entry(schedule_id: int):
    return query_one("SELECT * FROM medication_schedule WHERE id = ?", (schedule_id,))


def set_reminder(schedule_id: int, enabled: bool) -> None:
    execute(
        "UPDATE medication_schedule SET reminder_enabled = ? WHERE id = ?",
        (1 if enabled else 0, schedule_id),
    )


def delete_schedule_entry(schedule_id: int) -> None:
    execute("DELETE FROM medication_schedule WHERE id = ?", (schedule_id,))


def record_dose(schedule_id: int, patient_id: int, dose_date: str, dose_index: int, status: str):
    return execute(
        """INSERT INTO adherence_records (schedule_id, patient_id, dose_date, dose_index, status, recorded_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(schedule_id, dose_date, dose_index)
           DO UPDATE SET status = excluded.status, recorded_at = excluded.recorded_at""",
        (schedule_id, patient_id, dose_date, dose_index, status, now_iso()),
    )


def list_adherence(patient_id: int, start_date=None, end_date=None):
    sql = "SELECT * FROM adherence_records WHERE patient_id = ?"
    params = [patient_id]
    if start_date:
        sql += " AND dose_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND dose_date <= ?"
        params.append(end_date)
    return query(sql + " ORDER BY dose_date DESC, dose_index", tuple(params))


def adherence_for_schedule(schedule_id: int):
    return query(
        "SELECT * FROM adherence_records WHERE schedule_id = ? ORDER BY dose_date, dose_index",
        (schedule_id,),
    )


# --------------------------------------------------------------------------
# Chat history
# --------------------------------------------------------------------------


def save_chat(user_id: int, patient_id, role: str, message: str, sources=None):
    return execute(
        """INSERT INTO chat_history (user_id, patient_id, role, message, sources_json, created_at)
           VALUES (?,?,?,?,?,?)""",
        (user_id, patient_id, role, message, json.dumps(sources or []), now_iso()),
    )


def list_chat(user_id: int, patient_id=None, limit: int = 200):
    if patient_id:
        rows = query(
            """SELECT * FROM chat_history WHERE user_id = ? AND patient_id = ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, patient_id, limit),
        )
    else:
        rows = query(
            "SELECT * FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
    for row in rows:
        row["sources"] = json.loads(row.get("sources_json") or "[]")
    return list(reversed(rows))


def clear_chat(user_id: int, patient_id=None) -> None:
    if patient_id:
        execute("DELETE FROM chat_history WHERE user_id = ? AND patient_id = ?", (user_id, patient_id))
    else:
        execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
