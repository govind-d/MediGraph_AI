"""Medication Schedule page: today's doses, adherence, reminders."""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from backend import scheduler
from database import models
from frontend.components import footer, hero, inject_css, metric, require_patient
from utils.helpers import pretty_date


def render(user, patient) -> None:
    inject_css()
    hero("Medication Schedule",
         "Built only from the frequency and duration your prescription actually states.")
    if not require_patient(patient):
        footer()
        return

    entries = models.list_schedule(patient["id"])
    summary = scheduler.adherence_summary(patient["id"], days=7)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric("Active courses", sum(1 for e in entries if scheduler.course_status(e) == "active"),
               "%d total entries" % len(entries))
    with c2:
        metric("Doses today", len(scheduler.day_plan(patient["id"])), "scheduled slots")
    with c3:
        metric("Taken (7 days)", summary["taken"], "%d missed" % summary["missed"])
    with c4:
        metric("Adherence (7 days)", "%s%%" % summary["adherence_percent"],
               "of %d scheduled doses" % summary["scheduled"])

    tab_today, tab_upcoming, tab_history, tab_courses = st.tabs(
        ["Today", "Upcoming", "Adherence history", "Courses & reminders"]
    )

    with tab_today:
        plan = scheduler.day_plan(patient["id"])
        if not plan:
            st.info(
                "Nothing scheduled today. A schedule is only generated when the prescription "
                "states how many doses per day — otherwise the frequency must be confirmed first."
            )
        for slot in plan:
            c1, c2, c3 = st.columns([3.2, 1, 1])
            timing_note = ("" if slot["timing_specified"]
                           else " · clock time not specified in the prescription")
            c1.markdown(
                "**%s** — %s<br><span style='font-size:0.82rem;color:#5b6b7b'>%s · %s%s · status: "
                "<strong>%s</strong></span>"
                % (slot["medication_name"], slot.get("dose") or "dose as prescribed",
                   slot["dose_label"], slot.get("frequency_text") or "as written", timing_note,
                   slot["status"]),
                unsafe_allow_html=True,
            )
            key = "%d_%s_%d" % (slot["schedule_id"], slot["date"], slot["dose_index"])
            if c2.button("✓ Taken", key="t_" + key):
                scheduler.mark_dose(patient["id"], slot["schedule_id"], slot["date"],
                                    slot["dose_index"], "taken")
                st.rerun()
            if c3.button("✗ Missed", key="m_" + key):
                scheduler.mark_dose(patient["id"], slot["schedule_id"], slot["date"],
                                    slot["dose_index"], "missed")
                st.rerun()
        guidance = scheduler.timing_guidance_for_patient(patient["id"])
        if guidance:
            st.markdown("**Food, alcohol and spacing rules for today's medicines**")
            for rule in guidance:
                icon = {"food": "🍽️", "spacing": "⏱️", "avoid": "🚫"}.get(rule.get("rule_type"), "ℹ️")
                st.markdown("- %s **%s** — %s _(%s)_"
                            % (icon, rule["medication"], rule["guidance"],
                               rule.get("source_name") or "local dataset"))

        reminders = scheduler.reminder_lines(patient["id"])
        if reminders:
            st.markdown("**Reminders for today**")
            for line in reminders:
                st.markdown("- %s" % line)

    with tab_upcoming:
        upcoming = scheduler.upcoming_doses(patient["id"], days=7)
        if not upcoming:
            st.info("No doses scheduled in the next 7 days.")
        current_day = None
        for slot in upcoming:
            if slot["date"] != current_day:
                current_day = slot["date"]
                st.markdown("**%s**" % pretty_date(current_day))
            st.markdown(
                "- %s — %s · %s" % (slot["medication_name"],
                                    slot.get("dose") or "dose as prescribed", slot["dose_label"])
            )

    with tab_history:
        history = summary["history"]
        if any(day["scheduled"] for day in history):
            st.bar_chart({day["date"][5:]: day["adherence_percent"] for day in history},
                         height=240, color="#27a79b")
        rows = [
            {"Date": pretty_date(day["date"]), "Scheduled": day["scheduled"],
             "Taken": day["taken"], "Missed": day["missed"], "Pending": day["pending"],
             "Adherence %": day["adherence_percent"]}
            for day in reversed(history)
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption("Adherence counts only doses this tool scheduled from the written frequency.")

    with tab_courses:
        if not entries:
            st.info("No schedule entries yet. Analyse a prescription with “Create a dose schedule” "
                    "ticked.")
        for entry in entries:
            status = scheduler.course_status(entry)
            with st.expander("%s — %s (%s)" % (entry["medication_name"],
                                               entry.get("frequency_text") or "frequency as written",
                                               status)):
                st.markdown(
                    "- **Dose:** %s\n- **Doses per day:** %s\n- **Start:** %s\n- **End:** %s\n"
                    "- **Clock times specified in the prescription:** %s"
                    % (entry.get("dose") or "not stated",
                       entry.get("doses_per_day"), pretty_date(entry["start_date"]),
                       pretty_date(entry["end_date"]) if entry.get("end_date")
                       else "open-ended (no duration stated)",
                       "yes" if entry.get("timing_specified") else "no — agree times with your pharmacist")
                )
                c1, c2 = st.columns(2)
                reminder = c1.checkbox("Reminders on", value=bool(entry.get("reminder_enabled")),
                                       key="rem_%d" % entry["id"])
                if reminder != bool(entry.get("reminder_enabled")):
                    models.set_reminder(entry["id"], reminder)
                    st.rerun()
                if c2.button("Remove entry", key="delsched_%d" % entry["id"]):
                    models.delete_schedule_entry(entry["id"])
                    st.rerun()
        with st.form("manual_schedule"):
            st.markdown("**Add a schedule entry manually** (for example after confirming a "
                        "frequency with your pharmacist)")
            prescriptions = models.list_prescriptions(patient["id"])
            if not prescriptions:
                st.caption("Save a prescription first.")
                st.form_submit_button("Add", disabled=True)
            else:
                options = {"#%s — %s" % (p["id"], str(p["created_at"])[:10]): p["id"]
                           for p in prescriptions}
                c1, c2, c3 = st.columns(3)
                rx_label = c1.selectbox("Prescription", list(options.keys()))
                name = c2.text_input("Medication name")
                per_day = c3.number_input("Doses per day", min_value=0.5, max_value=8.0,
                                          value=2.0, step=0.5)
                c4, c5, c6 = st.columns(3)
                dose = c4.text_input("Dose (as written)")
                start = c5.date_input("Start date", value=date.today())
                days = c6.number_input("Duration (days, 0 = open-ended)", min_value=0,
                                       max_value=365, value=5)
                if st.form_submit_button("Add entry"):
                    if not name.strip():
                        st.error("Enter the medication name.")
                    else:
                        end = start + timedelta(days=int(days) - 1) if days else None
                        models.create_schedule_entry(
                            patient["id"], options[rx_label], None, name.strip(), dose.strip(),
                            "%s doses per day (entered manually)" % per_day, per_day,
                            start.isoformat(), end.isoformat() if end else None,
                            timing_specified=False,
                        )
                        st.success("Schedule entry added.")
                        st.rerun()
    footer()
