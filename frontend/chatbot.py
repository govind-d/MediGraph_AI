"""AI Medical Chatbot page (retrieval-augmented, cited, guard-railed)."""
from __future__ import annotations

import streamlit as st

from backend import pipeline, rag_engine
from database import models
from frontend.components import (disclaimer_box, esc, footer, hero, inject_css, require_patient,
                                 source_mode_pill)
from utils.config import Config
from utils.security import sanitize_text


def render(user, patient) -> None:
    inject_css()
    hero("AI Medical Chatbot",
         "Answers are built from your prescription records and a cited reference library.")
    disclaimer_box()
    if not require_patient(patient):
        footer()
        return

    outcome = st.session_state.get("analysis") or {}
    context = pipeline.rag_context(
        patient["id"],
        items=outcome.get("items"),
        report=outcome.get("report"),
        graph_payload=(outcome.get("graph") or {}).get("payload"),
    )

    c1, c2 = st.columns([1, 1])
    with c1:
        source_mode_pill("Generation: %s" % ("LLM (%s)" % Config.LLM_PROVIDER
                                             if Config.llm_enabled() else "extractive (no LLM key)"))
    with c2:
        source_mode_pill("Context documents: %d" % len(rag_engine.build_corpus(context)))

    st.caption("The chatbot does not diagnose conditions and will not tell you to start, stop or "
               "change a medicine. Those questions are redirected to your prescriber.")

    history = models.list_chat(user["id"], patient["id"], limit=60)
    for message in history:
        with st.chat_message("user" if message["role"] == "user" else "assistant"):
            st.markdown(message["message"])
            if message.get("sources"):
                with st.expander("Sources used"):
                    for source in message["sources"]:
                        _source_line(source)

    st.markdown("**Suggested questions**")
    cols = st.columns(2)
    for i, question in enumerate(rag_engine.SUGGESTED_QUESTIONS):
        if cols[i % 2].button(question, key="sq_%d" % i, use_container_width=True):
            _handle(user, patient, question, context)
            st.rerun()

    typed = st.chat_input("Ask about your prescription, a medicine, or a flagged interaction…")
    if typed:
        _handle(user, patient, sanitize_text(typed, 800), context)
        st.rerun()

    if history:
        if st.button("Clear this conversation"):
            models.clear_chat(user["id"], patient["id"])
            st.rerun()
    footer()


def _handle(user, patient, question: str, context) -> None:
    models.save_chat(user["id"], patient["id"], "user", question)
    result = rag_engine.answer_question(question, context)
    answer = result["answer"]
    if result["refused"]:
        answer = "⚠️ %s" % answer
    footer_line = "\n\n_Retrieval: %s_" % result["engine"] if result["retrieved"] else ""
    models.save_chat(user["id"], patient["id"], "assistant", answer + footer_line,
                     sources=result["sources"])


def _source_line(source) -> None:
    title = esc(source.get("title") or "source")
    name = esc(source.get("source_name") or "not recorded")
    if source.get("source_url"):
        st.markdown("- **%s** — %s ([link](%s))" % (title, name, source["source_url"]))
    else:
        st.markdown("- **%s** — %s" % (title, name))
