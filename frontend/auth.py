"""Login and registration page."""
from __future__ import annotations

import streamlit as st

from database import models
from frontend.components import disclaimer_box, footer, inject_css
from utils.security import (log_info, sanitize_text, validate_email, validate_password,
                            validate_username)


def render() -> None:
    inject_css()
    left, right = st.columns([1.05, 1])
    with left:
        st.markdown(
            '<div class="mg-hero"><h1>MediGraph AI</h1>'
            '<p>From a handwritten script to a connected safety check.</p></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="mg-card"><h4>What this does</h4>'
            "<p style='font-size:0.9rem;color:#1f2d3d'>Enter a prescription in plain language. "
            "MediGraph AI structures it, normalises each medicine against an RxNorm-derived "
            "dataset, maps it into a medical knowledge graph, checks interactions against your "
            "recorded allergies and conditions, and explains every finding with its source. "
            "An AI chatbot answers follow-up questions using only retrieved, cited material."
            "</p></div>",
            unsafe_allow_html=True,
        )
        disclaimer_box()

    with right:
        tab_login, tab_register = st.tabs(["Sign in", "Create an account"])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", key="login_user")
                password = st.text_input("Password", type="password", key="login_pass")
                submitted = st.form_submit_button("Sign in", use_container_width=True)
            if submitted:
                user = models.authenticate(sanitize_text(username, 64), password or "")
                if user:
                    st.session_state.user = user
                    st.session_state.page = "Dashboard"
                    log_info("User signed in")
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
            st.caption(
                "Demo account (created by `python seed_demo.py`): **demo** / **demo1234**"
            )

        with tab_register:
            with st.form("register_form"):
                new_user = st.text_input("Username", key="reg_user")
                full_name = st.text_input("Your name (optional)", key="reg_name")
                email = st.text_input("Email (optional)", key="reg_email")
                pw1 = st.text_input("Password", type="password", key="reg_pw1")
                pw2 = st.text_input("Confirm password", type="password", key="reg_pw2")
                agreed = st.checkbox(
                    "I understand MediGraph AI is an educational tool and does not replace a "
                    "doctor or pharmacist."
                )
                created = st.form_submit_button("Create account", use_container_width=True)
            if created:
                errors = []
                ok, msg = validate_username(new_user or "")
                if not ok:
                    errors.append(msg)
                ok, msg = validate_password(pw1 or "")
                if not ok:
                    errors.append(msg)
                if pw1 != pw2:
                    errors.append("The two passwords do not match.")
                ok, msg = validate_email(email or "")
                if not ok:
                    errors.append(msg)
                if not agreed:
                    errors.append("Please acknowledge the disclaimer.")
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    uid, err = models.create_user(
                        sanitize_text(new_user, 64), pw1,
                        sanitize_text(email, 120), sanitize_text(full_name, 120),
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Account created. Signing you in…")
                        st.session_state.user = models.authenticate(new_user.strip(), pw1)
                        st.session_state.page = "Patient Profiles"
                        st.rerun()
            st.caption(
                "Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a per-user random salt. "
                "Use dummy or anonymised data in this prototype."
            )
    footer()
