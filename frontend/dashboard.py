"""Dashboard page: counts, alerts, upcoming doses and recent activity."""
from __future__ import annotations

import streamlit as st

from backend import pipeline, scheduler
from database import models
from frontend.components import (disclaimer_box, footer, hero, metric, require_patient,
                                 severity_badge)
from utils.helpers import pretty_datetime, pretty_date


def render(user, patient) -> None:
    hero("Dashboard",
         "Overview for %s" % (patient["name"] if patient else "your account"))
    disclaimer_box()
    if not require_patient(patient):
        footer()
        return

    scheduler.complete_finished_courses(patient["id"])
    data = pipeline.dashboard_metrics(patient["id"])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric("Total prescriptions", data["total_prescriptions"],
               "%d active" % data["active_prescriptions"])
    with c2:
        metric("Current medications", data["current_medications"],
               "from active prescriptions")
    with c3:
        metric("Interaction alerts", data["interaction_alerts"],
               "%d report(s) with a high-concern finding" % data["high_alerts"])
    with c4:
        metric("Doses due (48h)", data["upcoming_doses"],
               "%s%% adherence this week" % data["adherence"]["adherence_percent"])

    st.write("")
    left, right = st.columns([1.15, 1])

    with left:
        st.subheader("Upcoming scheduled doses")
        if not data["upcoming"]:
            st.info(
                "No scheduled doses in the next 48 hours. A schedule is only created when the "
                "prescription states how many doses per day."
            )
        for slot in data["upcoming"]:
            note = "" if slot["timing_specified"] else " · exact clock time not specified in the prescription"
            st.markdown(
                '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">'
                '<strong>%s</strong> — %s<br>'
                '<span style="font-size:0.84rem;color:#5b6b7b">%s · %s · %s%s</span></div>'
                % (slot["medication_name"], slot.get("dose") or "dose as prescribed",
                   pretty_date(slot["date"]), slot["dose_label"],
                   slot.get("frequency_text") or "frequency as written", note),
                unsafe_allow_html=True,
            )

        st.subheader("Recent prescription activity")
        if not data["recent_prescriptions"]:
            st.info("No prescriptions saved yet. Use **Enter Prescription** to analyse one.")
        for rx in data["recent_prescriptions"]:
            meds = models.list_prescription_medications(rx["id"])
            names = ", ".join(filter(None, (m["medication_text"] for m in meds))) or "no medications parsed"
            st.markdown(
                '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">'
                '<strong>Prescription #%s</strong> <span class="mg-pill">%s</span><br>'
                '<span style="font-size:0.86rem">%s</span><br>'
                '<span style="font-size:0.78rem;color:#5b6b7b">Saved %s</span></div>'
                % (rx["id"], rx["status"], names, pretty_datetime(rx["created_at"])),
                unsafe_allow_html=True,
            )

    with right:
        st.subheader("Potential interaction alerts")
        if not data["recent_reports"]:
            st.info("No safety reports yet.")
        for report in data["recent_reports"]:
            st.markdown(
                '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">%s<br>'
                '<span style="font-size:0.84rem">%d finding(s) · report #%s</span><br>'
                '<span style="font-size:0.78rem;color:#5b6b7b">%s</span></div>'
                % (severity_badge(report.get("highest_severity")), report.get("finding_count") or 0,
                   report["id"], pretty_datetime(report["created_at"])),
                unsafe_allow_html=True,
            )

        st.subheader("Adherence this week")
        history = data["adherence"]["history"]
        if any(day["scheduled"] for day in history):
            st.bar_chart(
                {day["date"][5:]: day["adherence_percent"] for day in history},
                height=220, color="#2b8ca8",
            )
            st.caption(
                "%d of %d scheduled doses marked taken; %d marked missed."
                % (data["adherence"]["taken"], data["adherence"]["scheduled"],
                   data["adherence"]["missed"])
            )
        else:
            st.info("No doses scheduled in the last 7 days.")

        st.subheader("Profile summary")
        allergies = models.list_allergies(patient["id"])
        conditions = models.list_conditions(patient["id"])
        st.markdown(
            '<div class="mg-card" style="padding:12px 16px">'
            '<strong>%s</strong> · %s · age %s<br>'
            '<span style="font-size:0.84rem"><strong>Allergies:</strong> %s</span><br>'
            '<span style="font-size:0.84rem"><strong>Conditions:</strong> %s</span></div>'
            % (patient["name"], patient.get("relationship") or "-",
               patient.get("age") if patient.get("age") is not None else "not recorded",
               ", ".join(a["substance"] for a in allergies) or "none recorded",
               ", ".join(c["condition_name"] for c in conditions) or "none recorded"),
            unsafe_allow_html=True,
        )
    footer()
