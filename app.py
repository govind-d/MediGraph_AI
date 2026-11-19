"""MediGraph AI - Streamlit entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="MediGraph AI",
    page_icon="⚕️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from database.db import init_db  # noqa: E402  (after set_page_config)
from database import models  # noqa: E402
from frontend import auth, chatbot, dashboard, history, prescription, profiles, schedule, settings  # noqa: E402
from frontend.components import inject_css  # noqa: E402
from utils.config import Config  # noqa: E402
from utils.security import log_error  # noqa: E402

PAGES = [
    "Dashboard",
    "Patient Profiles",
    "Enter Prescription",
    "Prescription Analysis",
    "Drug Interaction Report",
    "AI Medical Chatbot",
    "Medication Schedule",
    "Medication History",
    "Settings",
]

PAGE_ICONS = {
    "Dashboard": "📊",
    "Patient Profiles": "👥",
    "Enter Prescription": "📝",
    "Prescription Analysis": "🔬",
    "Drug Interaction Report": "🛡️",
    "AI Medical Chatbot": "💬",
    "Medication Schedule": "🗓️",
    "Medication History": "📚",
    "Settings": "⚙️",
}


@st.cache_resource
def bootstrap():
    init_db()
    return True


def current_patient(user):
    profiles_list = models.list_profiles(user["id"])
    if not profiles_list:
        return None, []
    pid = st.session_state.get("patient_id")
    chosen = next((p for p in profiles_list if p["id"] == pid), None)
    if chosen is None:
        chosen = profiles_list[0]
        st.session_state.patient_id = chosen["id"]
    return chosen, profiles_list


def sidebar(user, patient, profiles_list) -> str:
    with st.sidebar:
        st.markdown(
            "<div style='padding:6px 0 10px 0'>"
            "<div style='font-size:1.25rem;font-weight:700;color:#15657f'>⚕️ MediGraph AI</div>"
            "<div style='font-size:0.78rem;color:#5b6b7b'>Prescription intelligence</div></div>",
            unsafe_allow_html=True,
        )
        if profiles_list:
            names = {"%s (%s)" % (p["name"], p.get("relationship") or "-"): p["id"]
                     for p in profiles_list}
            labels = list(names.keys())
            index = next((i for i, label in enumerate(labels)
                          if names[label] == (patient or {}).get("id")), 0)
            selected = st.selectbox("Patient profile", labels, index=index)
            if names[selected] != st.session_state.get("patient_id"):
                st.session_state.patient_id = names[selected]
                st.session_state.pop("analysis", None)
                st.rerun()
        else:
            st.info("Add a patient profile to begin.")

        st.markdown("---")
        current = st.session_state.get("page", "Dashboard")
        for page in PAGES:
            if st.button("%s  %s" % (PAGE_ICONS[page], page), key="nav_%s" % page,
                         use_container_width=True,
                         type="primary" if page == current else "secondary"):
                st.session_state.page = page
                st.rerun()

        st.markdown("---")
        st.caption("Signed in as **%s**" % user["username"])
        if st.button("Sign out", use_container_width=True):
            for key in ("user", "patient_id", "analysis"):
                st.session_state.pop(key, None)
            st.rerun()
        st.caption(
            "Graph: %s · Chatbot: %s"
            % ("Neo4j" if Config.USE_NEO4J else "local",
               "LLM" if Config.llm_enabled() else "extractive")
        )
    return st.session_state.get("page", "Dashboard")


def main() -> None:
    bootstrap()
    inject_css()
    st.session_state.setdefault("page", "Dashboard")

    user = st.session_state.get("user")
    if not user:
        auth.render()
        return

    patient, profiles_list = current_patient(user)
    page = sidebar(user, patient, profiles_list)

    try:
        if page == "Dashboard":
            dashboard.render(user, patient)
        elif page == "Patient Profiles":
            profiles.render(user, patient)
        elif page == "Enter Prescription":
            prescription.render_entry(user, patient)
        elif page == "Prescription Analysis":
            prescription.render_analysis(user, patient)
        elif page == "Drug Interaction Report":
            prescription.render_report(user, patient)
        elif page == "AI Medical Chatbot":
            chatbot.render(user, patient)
        elif page == "Medication Schedule":
            schedule.render(user, patient)
        elif page == "Medication History":
            history.render(user, patient)
        elif page == "Settings":
            settings.render(user, patient)
        else:
            dashboard.render(user, patient)
    except Exception as exc:  # pragma: no cover - last-resort UI guard
        log_error("rendering page %s" % page, exc)
        st.error(
            "Something went wrong while rendering this page. The details were written to "
            "`medigraph.log` without any patient information. Error type: %s" % type(exc).__name__
        )


if __name__ == "__main__":
    main()
