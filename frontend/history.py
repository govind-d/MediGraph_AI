"""Medication History page: prescriptions, courses, reports and past chats by date."""
from __future__ import annotations

import streamlit as st

from backend import scheduler
from database import models
from frontend import prescription as prescription_page
from frontend.components import (esc, footer, hero, inject_css, require_patient, severity_badge)
from utils.helpers import pretty_date, pretty_datetime


def render(user, patient) -> None:
    inject_css()
    hero("Medication History", "Previous prescriptions, completed courses, reports and chats.")
    if not require_patient(patient):
        footer()
        return

    scheduler.complete_finished_courses(patient["id"])
    prescriptions = models.list_prescriptions(patient["id"])
    reports = models.list_safety_reports(patient["id"], limit=100)
    chats = models.list_chat(user["id"], patient["id"], limit=400)

    tab_current, tab_past, tab_reports, tab_chats = st.tabs(
        ["Current medications", "Prescriptions (%d)" % len(prescriptions),
         "Interaction reports (%d)" % len(reports), "Chat history"]
    )

    with tab_current:
        current = models.current_medications(patient["id"])
        if not current:
            st.info("No active prescriptions.")
        for row in current:
            st.markdown(
                '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">'
                "<strong>%s</strong> %s<br>"
                '<span style="font-size:0.84rem">%s · %s · %s</span><br>'
                '<span style="font-size:0.78rem;color:#5b6b7b">From prescription #%s, saved %s</span></div>'
                % (esc(row["medication_text"]), esc(row.get("strength") or ""),
                   esc(row.get("dose") or "dose not stated"),
                   esc(row.get("frequency") or "frequency not stated"),
                   esc(row.get("duration") or "duration not stated"),
                   row["rx_id"], pretty_datetime(row.get("rx_created_at"))),
                unsafe_allow_html=True,
            )

    with tab_past:
        if not prescriptions:
            st.info("No prescriptions saved yet.")
        dates = sorted({str(p["created_at"])[:10] for p in prescriptions}, reverse=True)
        chosen = st.multiselect("Filter by date", dates, default=dates[:5] if dates else [])
        status_filter = st.multiselect("Status", ["active", "completed", "archived"],
                                       default=["active", "completed", "archived"])
        for rx in prescriptions:
            if chosen and str(rx["created_at"])[:10] not in chosen:
                continue
            if rx["status"] not in status_filter:
                continue
            meds = models.list_prescription_medications(rx["id"])
            scanned = (rx.get("source") or "typed") == "ocr"
            with st.expander(
                "#%s · %s · %s · %d medication(s)%s"
                % (rx["id"], pretty_datetime(rx["created_at"]), rx["status"], len(meds),
                   " · 📷 scanned" if scanned else "")
            ):
                if scanned:
                    st.caption(
                        "Transcribed by %s%s and confirmed by the user before analysis."
                        % (rx.get("ocr_engine") or "an OCR engine",
                           " at %.0f%% average confidence" % rx["ocr_confidence"]
                           if rx.get("ocr_confidence") else "")
                    )
                st.code((rx["raw_text"] or "").strip() or "(no text stored)")
                rows = [
                    {"Medication": m["medication_text"], "Strength": m.get("strength") or "-",
                     "Dose": m.get("dose") or "-", "Frequency": m.get("frequency") or "-",
                     "Duration": m.get("duration") or "-", "Route": m.get("route") or "-"}
                    for m in meds
                ]
                if rows:
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                c1, c2, c3 = st.columns(3)
                if rx["status"] == "active" and c1.button("Mark course completed",
                                                          key="comp_%d" % rx["id"]):
                    models.set_prescription_status(rx["id"], "completed")
                    st.rerun()
                if rx["status"] != "archived" and c2.button("Archive", key="arch_%d" % rx["id"]):
                    models.set_prescription_status(rx["id"], "archived")
                    st.rerun()
                if c3.button("Delete", key="delrx_%d" % rx["id"]):
                    st.session_state["confirm_rx_%d" % rx["id"]] = True
                if st.session_state.get("confirm_rx_%d" % rx["id"]):
                    st.warning("Deleting prescription #%s also removes its schedule entries, "
                               "adherence records and safety reports. This cannot be undone."
                               % rx["id"])
                    cc1, cc2 = st.columns(2)
                    if cc1.button("Yes, delete", key="yesrx_%d" % rx["id"]):
                        models.delete_prescription(rx["id"])
                        st.session_state.pop("confirm_rx_%d" % rx["id"], None)
                        st.rerun()
                    if cc2.button("Cancel", key="norx_%d" % rx["id"]):
                        st.session_state.pop("confirm_rx_%d" % rx["id"], None)
                        st.rerun()

    with tab_reports:
        if not reports:
            st.info("No safety reports saved yet.")
        for report in reports:
            c1, c2 = st.columns([4, 1])
            c1.markdown(
                "%s · report **#%s** · %d finding(s) · %s"
                % (severity_badge(report.get("highest_severity")), report["id"],
                   report.get("finding_count") or 0, pretty_datetime(report["created_at"])),
                unsafe_allow_html=True,
            )
            if c2.button("Open", key="openrep_%d" % report["id"]):
                if prescription_page.load_stored_report(report["id"]):
                    st.session_state.page = "Drug Interaction Report"
                    st.rerun()

    with tab_chats:
        if not chats:
            st.info("No chatbot conversations saved for this profile yet.")
        dates = sorted({str(c["created_at"])[:10] for c in chats}, reverse=True)
        if dates:
            day = st.selectbox("Conversation date", dates)
            for message in [c for c in chats if str(c["created_at"])[:10] == day]:
                with st.chat_message("user" if message["role"] == "user" else "assistant"):
                    st.markdown(message["message"])
                    if message.get("sources"):
                        with st.expander("Sources"):
                            for source in message["sources"]:
                                st.markdown("- %s — %s" % (esc(source.get("title")),
                                                           esc(source.get("source_name"))))
            st.caption("Showing %s" % pretty_date(day))
    footer()
