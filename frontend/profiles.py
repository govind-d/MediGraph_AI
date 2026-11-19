"""Patient profiles page: create, edit, delete profiles, allergies and conditions."""
from __future__ import annotations

import streamlit as st

from database import models
from frontend.components import footer, hero, inject_css
from utils.security import sanitize_text

COMMON_ALLERGIES = ["penicillin", "amoxicillin", "sulfonamide", "nsaid", "aspirin", "ibuprofen",
                    "macrolide", "cephalosporin", "codeine", "morphine", "statin", "iodine",
                    "latex", "peanut"]
COMMON_CONDITIONS = ["asthma", "copd", "diabetes", "hypertension", "heart failure",
                     "chronic kidney disease", "liver disease", "peptic ulcer", "epilepsy",
                     "gout", "glaucoma", "pregnancy", "thyroid disorder", "depression"]


def render(user, patient) -> None:
    inject_css()
    hero("Patient Profiles",
         "Add a profile per person, including family members. Allergies and conditions drive the "
         "safety checks.")

    profiles = models.list_profiles(user["id"])
    tab_manage, tab_new = st.tabs(["Manage profiles (%d)" % len(profiles), "Add a profile"])

    with tab_new:
        with st.form("new_profile"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Name *")
            relationship = c2.selectbox(
                "Relationship", ["Self", "Spouse", "Child", "Parent", "Sibling", "Other"]
            )
            age = c3.number_input("Age", min_value=0, max_value=120, value=30, step=1)
            c4, c5, c6 = st.columns(3)
            sex = c4.selectbox("Sex", ["", "female", "male", "other", "prefer not to say"])
            weight = c5.number_input("Weight (kg, optional)", min_value=0.0, max_value=350.0,
                                     value=0.0, step=0.5)
            pregnant = c6.checkbox("Currently pregnant")
            notes = st.text_area("Notes (optional)", height=70,
                                 placeholder="Anything a pharmacist should know, e.g. swallowing difficulty.")
            submitted = st.form_submit_button("Create profile")
        if submitted:
            if not sanitize_text(name, 80):
                st.error("A name is required.")
            else:
                try:
                    pid = models.create_profile(
                        user["id"], sanitize_text(name, 80), relationship, int(age),
                        sex or None, weight or None, sanitize_text(notes, 600), pregnant,
                    )
                    st.session_state.patient_id = pid
                    st.success("Profile created for %s." % name)
                    st.rerun()
                except Exception as exc:
                    st.error("Could not create the profile: %s" % type(exc).__name__)
        st.caption("Use dummy or anonymised details in this prototype. Data stays in the local "
                   "SQLite database on this machine.")

    with tab_manage:
        if not profiles:
            st.info("No profiles yet. Add one in the next tab.")
        for profile in profiles:
            selected = patient and profile["id"] == patient["id"]
            with st.expander(
                "%s%s — %s, age %s"
                % ("✓ " if selected else "", profile["name"], profile.get("relationship") or "-",
                   profile.get("age") if profile.get("age") is not None else "not recorded"),
                expanded=bool(selected),
            ):
                _profile_editor(profile)
                st.divider()
                _allergy_editor(profile)
                st.divider()
                _condition_editor(profile)
                st.divider()
                c1, c2 = st.columns([1, 1])
                if c1.button("Use this profile", key="use_%d" % profile["id"]):
                    st.session_state.patient_id = profile["id"]
                    st.rerun()
                if c2.button("Delete profile", key="del_%d" % profile["id"],
                             help="Deletes the profile and all its prescriptions, reports and schedules."):
                    st.session_state["confirm_delete_%d" % profile["id"]] = True
                if st.session_state.get("confirm_delete_%d" % profile["id"]):
                    st.warning(
                        "This permanently deletes %s and every prescription, report, schedule and "
                        "chat record linked to it. This cannot be undone." % profile["name"]
                    )
                    cc1, cc2 = st.columns(2)
                    if cc1.button("Yes, delete permanently", key="yes_%d" % profile["id"]):
                        models.delete_profile(profile["id"])
                        if st.session_state.get("patient_id") == profile["id"]:
                            st.session_state.patient_id = None
                        st.session_state.pop("confirm_delete_%d" % profile["id"], None)
                        st.rerun()
                    if cc2.button("Cancel", key="no_%d" % profile["id"]):
                        st.session_state.pop("confirm_delete_%d" % profile["id"], None)
                        st.rerun()
    footer()


def _profile_editor(profile) -> None:
    with st.form("edit_%d" % profile["id"]):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Name", value=profile["name"])
        relationship = c2.text_input("Relationship", value=profile.get("relationship") or "")
        age = c3.number_input("Age", min_value=0, max_value=120,
                              value=int(profile.get("age") or 0), step=1)
        c4, c5, c6 = st.columns(3)
        sex = c4.text_input("Sex", value=profile.get("sex") or "")
        weight = c5.number_input("Weight (kg)", min_value=0.0, max_value=350.0,
                                 value=float(profile.get("weight_kg") or 0.0), step=0.5)
        pregnant = c6.checkbox("Currently pregnant", value=bool(profile.get("is_pregnant")))
        notes = st.text_area("Notes", value=profile.get("notes") or "", height=70)
        if st.form_submit_button("Save changes"):
            models.update_profile(
                profile["id"], name=sanitize_text(name, 80), relationship=sanitize_text(relationship, 40),
                age=int(age), sex=sanitize_text(sex, 30) or None, weight_kg=weight or None,
                notes=sanitize_text(notes, 600), is_pregnant=pregnant,
            )
            st.success("Profile updated.")
            st.rerun()


def _allergy_editor(profile) -> None:
    st.markdown("**Known allergies**")
    allergies = models.list_allergies(profile["id"])
    if not allergies:
        st.caption("None recorded. Allergy checks can only run on what is recorded here.")
    for allergy in allergies:
        c1, c2 = st.columns([5, 1])
        c1.markdown(
            "• **%s** — reaction: %s · severity: %s"
            % (allergy["substance"], allergy.get("reaction") or "not recorded",
               allergy.get("severity") or "unknown")
        )
        if c2.button("Remove", key="ral_%d" % allergy["id"]):
            models.delete_allergy(allergy["id"])
            st.rerun()
    with st.form("add_allergy_%d" % profile["id"]):
        c1, c2, c3 = st.columns([2, 2, 1])
        substance = c1.selectbox("Substance", [""] + COMMON_ALLERGIES, key="al_sel_%d" % profile["id"])
        custom = c1.text_input("…or type another", key="al_txt_%d" % profile["id"])
        reaction = c2.text_input("Reaction (e.g. rash, swelling, wheeze)")
        severity = c3.selectbox("Severity", ["unknown", "mild", "moderate", "severe"])
        if st.form_submit_button("Add allergy"):
            value = sanitize_text(custom or substance, 80)
            if not value:
                st.error("Choose or type a substance.")
            else:
                models.add_allergy(profile["id"], value, sanitize_text(reaction, 200), severity)
                st.rerun()


def _condition_editor(profile) -> None:
    st.markdown("**Medical conditions**")
    conditions = models.list_conditions(profile["id"])
    if not conditions:
        st.caption("None recorded. Condition cautions can only run on what is recorded here.")
    for condition in conditions:
        c1, c2 = st.columns([5, 1])
        c1.markdown("• **%s** — %s" % (condition["condition_name"], condition.get("status") or "active"))
        if c2.button("Remove", key="rc_%d" % condition["id"]):
            models.delete_condition(condition["id"])
            st.rerun()
    with st.form("add_condition_%d" % profile["id"]):
        c1, c2 = st.columns([3, 1])
        condition = c1.selectbox("Condition", [""] + COMMON_CONDITIONS,
                                 key="cond_sel_%d" % profile["id"])
        custom = c1.text_input("…or type another", key="cond_txt_%d" % profile["id"])
        status = c2.selectbox("Status", ["active", "resolved", "monitoring"])
        if st.form_submit_button("Add condition"):
            value = sanitize_text(custom or condition, 80)
            if not value:
                st.error("Choose or type a condition.")
            else:
                models.add_condition(profile["id"], value, status)
                st.rerun()
