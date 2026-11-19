"""Settings page: data sources, integrations status, account and data controls."""
from __future__ import annotations

import streamlit as st

from backend import knowledge_graph, ocr, parser
from database import models
from database.db import count_rows_safe, init_db
from frontend.components import card, esc, footer, hero, inject_css, source_mode_pill
from utils.config import DISCLAIMER, Config
from utils.security import validate_password


def render(user, patient) -> None:
    inject_css()
    hero("Settings", "Data sources, integrations and account controls.")

    st.subheader("Where each answer comes from")
    rows = "".join(
        "<p style='font-size:0.9rem'><strong>%s:</strong> %s</p>" % (esc(k), esc(v))
        for k, v in Config.describe_sources().items()
    )
    card("Active data sources", rows)

    c1, c2 = st.columns(2)
    with c1:
        card("Integration status",
             "<p style='font-size:0.88rem'><strong>Neo4j:</strong> %s</p>"
             "<p style='font-size:0.88rem'><strong>spaCy:</strong> %s</p>"
             "<p style='font-size:0.88rem'><strong>LLM provider:</strong> %s</p>"
             "<p style='font-size:0.88rem'><strong>OCR:</strong> %s</p>"
             % (esc(knowledge_graph.neo4j_status()), esc(parser.spacy_status()),
                esc(Config.LLM_PROVIDER if Config.llm_enabled()
                    else "%s (no API key configured — extractive answers in use)"
                         % Config.LLM_PROVIDER),
                esc(ocr.describe_engines())))
    with c2:
        counts = count_rows_safe()
        card("Local database",
             "<p style='font-size:0.88rem'>SQLite file: <code>%s</code></p>" % esc(Config.DB_PATH)
             + "".join("<p style='font-size:0.88rem'>%s: %s rows</p>" % (esc(k), v)
                       for k, v in counts.items()))

    st.subheader("How to enable the optional live sources")
    with st.expander("RxNorm (medication identification) — free, no API key"):
        st.markdown(
            "RxNorm is served by the US National Library of Medicine's RxNav API and needs no key.\n\n"
            "```\nUSE_RXNORM_API=true\nRXNORM_BASE_URL=https://rxnav.nlm.nih.gov/REST\n```\n"
            "Add those lines to your `.env` file and restart the app. Without them, names are "
            "resolved against the bundled RxNorm-derived dataset."
        )
    with st.expander("openFDA (drug label text) — free, optional key for higher rate limits"):
        st.markdown(
            "Request a key at <https://open.fda.gov/apis/authentication/>, then set:\n\n"
            "```\nUSE_OPENFDA_API=true\nOPENFDA_API_KEY=your_key_here\n```\n"
            "Unauthenticated requests work but are rate-limited to a few hundred per day."
        )
    with st.expander("Neo4j (knowledge graph persistence)"):
        st.markdown(
            "Install Neo4j Desktop or run the official container:\n\n"
            "```\ndocker run -d --name medigraph-neo4j -p 7474:7474 -p 7687:7687 "
            "-e NEO4J_AUTH=neo4j/your_password neo4j:5\n```\n"
            "Then set:\n\n```\nUSE_NEO4J=true\nNEO4J_URI=bolt://localhost:7687\n"
            "NEO4J_USER=neo4j\nNEO4J_PASSWORD=your_password\n```\n"
            "Without Neo4j, the graph is built in-process with NetworkX and all findings are "
            "identical."
        )
    with st.expander("LLM for the chatbot (Anthropic or OpenAI)"):
        st.markdown(
            "Get a key from <https://console.anthropic.com/> or "
            "<https://platform.openai.com/api-keys>, then set either:\n\n"
            "```\nLLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-ant-...\n"
            "ANTHROPIC_MODEL=claude-sonnet-4-5\n```\n\nor:\n\n"
            "```\nLLM_PROVIDER=openai\nOPENAI_API_KEY=sk-...\nOPENAI_MODEL=gpt-4o-mini\n```\n"
            "Keys are read from environment variables only and are never written into the "
            "frontend or the database. Without a key the chatbot still works: it answers "
            "extractively from the retrieved passages."
        )
    with st.expander("OCR for scanned prescriptions"):
        for engine in ocr.available_engines():
            st.markdown("%s **%s** — handwriting: %s. %s"
                        % ("✅" if engine["available"] else "⬜", engine["name"],
                           engine["handwriting"], engine["reason"]))
        st.markdown(
            """
Tesseract is the offline default. On Windows, install it from
<https://github.com/UB-Mannheim/tesseract/wiki>, then:

```
python -m pip install pytesseract pypdf
```

If Tesseract is not on PATH, point to it in `.env`:

```
TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe
```

For handwritten scripts, configure a vision LLM (see the LLM section above).
Images are only sent to it when you tick the consent box on the upload.
"""
        )

    with st.expander("spaCy model (optional NLP assist)"):
        st.markdown(
            "```\npython -m spacy download en_core_web_sm\n```\n"
            "The parser works without it — spaCy is only used as an extra hint when a medication "
            "name is not in the local vocabulary."
        )

    st.divider()
    st.subheader("Account")
    c1, c2 = st.columns(2)
    with c1:
        with st.form("change_password"):
            st.markdown("**Change password**")
            new1 = st.text_input("New password", type="password")
            new2 = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Update password"):
                ok, msg = validate_password(new1 or "")
                if not ok:
                    st.error(msg)
                elif new1 != new2:
                    st.error("The two passwords do not match.")
                else:
                    models.change_password(user["id"], new1)
                    st.success("Password updated.")
    with c2:
        st.markdown("**Signed in as**")
        st.markdown("- Username: `%s`" % esc(user["username"]))
        st.markdown("- Profiles: %d" % len(models.list_profiles(user["id"])))
        st.markdown("- Account created: %s" % esc(str(user.get("created_at"))[:19]))
        if st.button("Sign out"):
            for key in ("user", "patient_id", "analysis"):
                st.session_state.pop(key, None)
            st.rerun()

    st.divider()
    st.subheader("Data controls")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Re-seed reference datasets"):
            init_db()
            st.success("Interaction and medication datasets re-seeded from the JSON files.")
    with c2:
        st.caption("Patient data is stored only in the local SQLite file. Deleting a profile "
                   "removes its prescriptions, reports, schedules and chats.")

    st.divider()
    source_mode_pill("Educational prototype")
    st.caption(DISCLAIMER.replace("**", ""))
    footer()
