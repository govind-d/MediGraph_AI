"""Enter Prescription, Prescription Analysis and Drug Interaction Report pages."""
from __future__ import annotations

import json

from pathlib import Path

import streamlit as st

from backend import knowledge_graph, ocr, parser, pipeline, safety_report
from database import models
from frontend.components import (card, disclaimer_box, emergency_box, esc, finding_card, footer,
                                 graph_figure, hero, inject_css, parsed_table, require_patient,
                                 severity_badge, source_mode_pill)
from utils.config import DATA_DIR, Config
from utils.helpers import SEVERITY_LABEL, pretty_datetime, severity_full
from utils.security import sanitize_text


def _samples():
    try:
        return json.loads((DATA_DIR / "sample_prescriptions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# --------------------------------------------------------------------------
# Page: Enter Prescription
# --------------------------------------------------------------------------
def render_entry(user, patient) -> None:
    inject_css()
    hero("Enter Prescription",
         "Type the instructions exactly as written. Nothing is inferred that the text does not say.")
    disclaimer_box()
    if not require_patient(patient):
        footer()
        return

    tab_type, tab_scan = st.tabs(["✍️ Type or paste", "📷 Scan an image (OCR)"])

    with tab_scan:
        _render_ocr_intake()

    with tab_type:
        _render_text_entry(user, patient)
    footer()


def _render_text_entry(user, patient) -> None:
    samples = _samples()
    labels = ["(start from scratch)"] + ["%s" % s["label"] for s in samples]
    choice = st.selectbox("Load a sample prescription (for testing)", labels)
    if choice != labels[0]:
        sample = samples[labels.index(choice) - 1]
        st.caption("Demonstrates: %s" % sample["demonstrates"])
        if st.button("Load this sample into the text box"):
            st.session_state.rx_text = sample["text"]
            st.rerun()

    ocr_meta = st.session_state.get("ocr_meta")
    if st.session_state.pop("ocr_loaded_notice", False):
        st.success("Scanned text loaded below. Review it, tick the confirmation box and analyse.")
    if ocr_meta:
        st.info(
            "This text came from **%s** (%s). Check every character against the original image "
            "before analysing it." % (ocr_meta["engine_label"],
                                      "confidence %.0f%%" % ocr_meta["confidence"]
                                      if ocr_meta.get("confidence") is not None
                                      else "no confidence score")
        )

    # The widget owns `rx_text`, so loading a sample or an OCR transcription into
    # session state (followed by a rerun) is what updates the box.
    st.session_state.setdefault("rx_text", "")
    text = st.text_area(
        "Prescription text",
        height=170,
        key="rx_text",
        placeholder="Amoxicillin 500mg, take one capsule three times daily for five days.\n"
                    "Ibuprofen 400mg, one tablet three times a day after food for 7 days.",
        help="One medication per line works best. Abbreviations such as PO, BD, TDS, q8h and PRN "
             "are understood.",
    )
    c1, c2, c3 = st.columns([1.1, 1, 1])
    prescriber = c1.text_input("Prescriber (optional)")
    save = c2.checkbox("Save to this patient's record", value=True)
    make_schedule = c3.checkbox("Create a dose schedule", value=True)

    confirmed = True
    if ocr_meta:
        confirmed = st.checkbox(
            "I have checked this transcription against the original image, and every medicine, "
            "strength, dose and frequency is correct.",
            key="ocr_confirm",
        )

    if st.button("Analyse prescription", use_container_width=True):
        clean = sanitize_text(text, 8000)
        if not clean.strip():
            st.error("Enter the prescription text first.")
        elif len(clean.strip()) < 6:
            st.error("That is too short to be a prescription instruction.")
        elif not confirmed:
            st.error("Confirm that you have checked the scanned transcription before analysing it.")
        else:
            with st.spinner("Parsing, normalising, mapping the graph and checking interactions…"):
                outcome = pipeline.analyze(
                    clean, patient, save=save, build_schedule=make_schedule and save,
                    prescriber=sanitize_text(prescriber, 120),
                    source="ocr" if ocr_meta else "typed",
                    ocr_engine=(ocr_meta or {}).get("engine"),
                    ocr_confidence=(ocr_meta or {}).get("confidence"),
                    ocr_verified=bool(ocr_meta),
                )
            st.session_state.analysis = outcome
            if outcome["errors"]:
                for error in outcome["errors"]:
                    st.error(error)
            else:
                st.success("Analysis complete. See **Prescription Analysis** and "
                           "**Drug Interaction Report** in the sidebar.")
                st.session_state.page = "Prescription Analysis"
                st.rerun()

    with st.expander("How the parser reads your text"):
        st.markdown(
            "- **Medication** is matched against a local RxNorm-derived vocabulary of generic "
            "and brand names; unknown names are kept but cannot be interaction-checked.\n"
            "- **Strength** (500mg, 10 mcg, 5ml), **dose** (one capsule, 2 puffs), "
            "**frequency** (three times daily, BD, q8h, every 6 hours, PRN), "
            "**duration** (for 5 days, x 30 days) and **route** (PO, inhaler, SC) are extracted "
            "with bounded patterns.\n"
            "- Anything the text does not state is listed as **needs verification** — the parser "
            "never fills in a plausible value.\n"
            "- spaCy status: `%s`" % parser.spacy_status()
        )


# --------------------------------------------------------------------------
# OCR intake
# --------------------------------------------------------------------------
def _render_ocr_intake() -> None:
    status = ocr.engine_status()

    st.markdown(
        "Upload a photo or scan of a prescription. MediGraph AI transcribes it, then you "
        "**check and correct the text** before anything is analysed. Nothing is analysed "
        "automatically from an image."
    )

    with st.expander("OCR engines on this machine", expanded=not status["any_available"]):
        for engine in status["engines"]:
            mark = "✅" if engine["available"] else "⬜"
            st.markdown(
                "%s **%s** — handwriting: %s%s"
                % (mark, engine["name"], engine["handwriting"],
                   "<br><span style='font-size:0.8rem;color:#5b6b7b'>%s</span>" % esc(engine["reason"])
                   if engine["reason"] else ""),
                unsafe_allow_html=True,
            )
    if not status["any_available"]:
        st.warning("No OCR engine is available yet. Follow the instructions above, or type the "
                   "prescription in the other tab.")

    uploaded = st.file_uploader(
        "Prescription image or PDF",
        type=list(ocr.SUPPORTED_TYPES),
        help="Photograph just the medication list, in focus, well lit and straight on.",
    )

    options = [e for e in status["engines"] if e["available"]]
    choice_labels = ["Automatic (best available)"] + [e["name"] for e in options]
    c1, c2 = st.columns([1.3, 1])
    engine_label = c1.selectbox("Engine", choice_labels)
    preprocess_image = c2.checkbox("Clean up the image first", value=True,
                                   help="Greyscale, upscale, contrast and denoise before OCR.")
    engine_id = "auto"
    if engine_label != choice_labels[0]:
        engine_id = options[choice_labels.index(engine_label) - 1]["id"]

    vision_available = any(e["id"] == "vision-llm" and e["available"] for e in status["engines"])
    allow_cloud = False
    if vision_available:
        allow_cloud = st.checkbox(
            "Allow sending this image to %s for transcription (it leaves this machine)"
            % Config.LLM_PROVIDER,
            value=False,
        )

    if uploaded is None:
        st.caption("Tip: a straight, cropped photo of the medication list reads far better than "
                   "a whole page at an angle.")
        return

    file_bytes = uploaded.getvalue()
    preview, result_col = st.columns([1, 1.25])
    with preview:
        if not uploaded.name.lower().endswith(".pdf"):
            st.image(file_bytes, caption="Uploaded image", use_container_width=True)
        else:
            st.info("PDF uploaded (%.0f KB). The embedded text layer is read when present."
                    % (len(file_bytes) / 1024))

    with result_col:
        if st.button("Transcribe this prescription", use_container_width=True):
            with st.spinner("Reading the image…"):
                result = ocr.extract_text(
                    file_bytes, uploaded.name, engine=engine_id,
                    preprocess_image=preprocess_image, allow_cloud=allow_cloud,
                )
            st.session_state.ocr_result = result

        result = st.session_state.get("ocr_result")
        if not result:
            st.caption("Press **Transcribe** to read this file.")
            return

        for error in result["errors"]:
            st.error(error)
        if not result["ok"]:
            return

        st.success("Transcribed %d line(s) with %s."
                   % (result["lines_detected"], result["engine_label"]))
        if result.get("confidence") is not None:
            st.progress(min(1.0, max(0.0, result["confidence"] / 100)),
                        text="Average character confidence: %.0f%%" % result["confidence"])
        for warning in result["warnings"]:
            st.warning(warning)
        if result["notes"]:
            st.caption("Image preparation: %s." % "; ".join(result["notes"]))

    st.markdown("#### Check and correct the transcription")
    st.caption("Compare every line against the image above. Fix anything the scanner got wrong, "
               "then send it to the analysis tab.")
    result = st.session_state.get("ocr_result")
    if not result or not result.get("ok"):
        return

    edited = st.text_area("Transcribed text (editable)", value=result["text"], height=200,
                          key="ocr_edit_area")
    if result["low_confidence_words"]:
        st.markdown("**Words the scanner was unsure about:** " + ", ".join(
            "`%s`%s" % (w["word"], " (%.0f%%)" % w["confidence"]
                        if w.get("confidence") is not None else "")
            for w in result["low_confidence_words"][:20]
        ))

    c1, c2 = st.columns([1.4, 1])
    if c1.button("Use this text in the prescription form", use_container_width=True):
        st.session_state.rx_text = sanitize_text(edited, 8000)
        st.session_state.ocr_meta = {
            "engine": result["engine"],
            "engine_label": result["engine_label"],
            "confidence": result.get("confidence"),
        }
        st.session_state.ocr_loaded_notice = True
        st.rerun()
    if c2.button("Discard this scan", use_container_width=True):
        st.session_state.pop("ocr_result", None)
        st.session_state.pop("ocr_meta", None)
        st.rerun()


# --------------------------------------------------------------------------
# Page: Prescription Analysis
# --------------------------------------------------------------------------
def render_analysis(user, patient) -> None:
    inject_css()
    hero("Prescription Analysis", "Structured output, normalisation and the knowledge graph.")
    outcome = st.session_state.get("analysis")
    if not outcome or not outcome.get("items"):
        st.info("Analyse a prescription first (Enter Prescription), or open a saved one from "
                "Medication History.")
        footer()
        return

    items = outcome["items"]
    parse_info = outcome["parse"]["parser"]

    st.subheader("Parsed prescription")
    parsed_table(items)
    for warning in outcome.get("warnings", []):
        st.warning(warning)

    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("Structured JSON")
        st.code(json.dumps(parser.to_simple_json(items), indent=2), language="json")
    with c2:
        st.subheader("Medication identification")
        for item in items:
            st.markdown(
                '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">'
                "<strong>%s</strong><br>"
                '<span style="font-size:0.84rem">Class: %s · RXCUI: %s</span><br>'
                '<span style="font-size:0.84rem">Generally used for: %s</span><br>'
                '<span style="font-size:0.78rem;color:#5b6b7b">Identified via: %s%s</span></div>'
                % (esc(item.get("display_name") or item.get("medication")),
                   esc(item.get("drug_class") or "not recorded"),
                   esc(item.get("rxcui") or "not available"),
                   esc(item.get("common_uses") or "not recorded in the dataset"),
                   esc(item.get("normalization_source") or "unknown"),
                   " (approximate name match — confirm spelling)"
                   if item.get("match_method") == "fuzzy" else ""),
                unsafe_allow_html=True,
            )
        source_mode_pill("Parser: %s" % parse_info.get("engine"))
        source_mode_pill("spaCy: %s" % parse_info.get("spacy"))
        source_mode_pill("LLM assist: %s" % ("on" if parse_info.get("llm_assist") else "off"))

    st.subheader("Medical knowledge graph")
    graph = outcome.get("graph") or {}
    if graph:
        stats = graph["stats"]
        st.caption(
            "Backend: %s · %d nodes · %d edges · %d medication node(s) · %d interaction node(s)"
            % (graph["backend"], stats["nodes"], stats["edges"], stats["medications"],
               stats["interactions"])
        )
        graph_figure(graph["payload"])
        with st.expander("Relationships in words"):
            for line in knowledge_graph.describe_graph(graph["payload"]):
                st.markdown("- %s" % line)
        with st.expander("Export as Cypher (to load into Neo4j manually)"):
            script = knowledge_graph.cypher_script(graph["payload"])
            st.code(script[:4000], language="cypher")
            st.download_button("Download graph.cypher", script, file_name="medigraph_graph.cypher")
    footer()


# --------------------------------------------------------------------------
# Page: Drug Interaction Report
# --------------------------------------------------------------------------
def render_report(user, patient) -> None:
    inject_css()
    hero("Drug Interaction Report", "Every finding carries its source and its limitations.")
    outcome = st.session_state.get("analysis")
    if not outcome or not outcome.get("report"):
        st.info("Analyse a prescription first, or open a saved report from Medication History.")
        footer()
        return

    report = outcome["report"]
    findings = outcome["findings"]
    counts = findings["counts"]

    st.markdown(
        '<div class="mg-card"><h4>Summary</h4><p>%s</p>%s</div>'
        % (esc(report["summary"]), severity_badge(findings["highest_severity"])),
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    for col, key in zip((c1, c2, c3), ("high", "moderate", "informational")):
        with col:
            st.markdown(
                '<div class="mg-metric"><div class="label">%s</div><div class="value">%d</div></div>'
                % (severity_full(key), counts.get(key, 0)), unsafe_allow_html=True)
    with c4:
        st.markdown(
            '<div class="mg-metric"><div class="label">Pairs checked</div><div class="value">%d</div>'
            '<div class="hint">%d medication(s)</div></div>'
            % (len(findings["checked_pairs"]), len(outcome["items"])), unsafe_allow_html=True)

    st.write("")
    dose_warnings = findings.get("dose_warnings") or []
    dose_alerts = [d for d in dose_warnings if d["severity"] != "none"]
    timing = findings.get("timing_guidance") or []
    tab1, tab2, tab3, tab5, tab6, tab4 = st.tabs(
        ["Drug interactions (%d)" % len(findings["interactions"]),
         "Allergy warnings (%d)" % len(findings["allergy_warnings"]),
         "Condition warnings (%d)" % len(findings["condition_warnings"]),
         "Dosage check (%d)" % len(dose_alerts),
         "Food & timing (%d)" % len(timing),
         "Method, sources & limits"]
    )

    with tab1:
        if not findings["interactions"]:
            st.success(
                "**No known interaction found in the checked sources** for the %d medication pair(s) "
                "examined. This is not a statement that the combination is safe — see "
                "*Method, sources & limits*." % len(findings["checked_pairs"])
            )
        for finding in findings["interactions"]:
            finding_card(finding)
        if findings["unchecked_medications"]:
            st.warning(
                "Not interaction-checked (not found in the drug dataset): %s. No result — positive "
                "or negative — applies to these."
                % ", ".join(str(m) for m in findings["unchecked_medications"])
            )

    with tab2:
        if not findings["allergy_warnings"]:
            st.success("No conflict found between the recorded allergies and these medications.")
            st.caption("Allergy checking only covers allergies recorded in the patient profile.")
        for finding in findings["allergy_warnings"]:
            finding_card(finding)

    with tab3:
        if not findings["condition_warnings"]:
            st.success("No condition-specific caution found for the recorded conditions.")
        for finding in findings["condition_warnings"]:
            finding_card(finding)

    with tab5:
        if not dose_warnings:
            st.info("No daily dose could be totalled. A dose is only checked when the "
                    "prescription states the strength and how many doses per day.")
        for finding in dose_warnings:
            if finding["severity"] == "none":
                st.markdown(
                    '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">'
                    '<span class="mg-badge" style="background:#2f9e6e">🟢 Within labelled '
                    "maximum</span> <strong>%s</strong><br>"
                    '<span style="font-size:0.86rem">%s</span><br>'
                    '<span style="font-size:0.78rem;color:#5b6b7b">Working: %s · Source: %s</span>'
                    "</div>"
                    % (esc(finding["medication"]),
                       esc(finding["description"]), esc(finding.get("evidence")),
                       esc(finding.get("source_name"))),
                    unsafe_allow_html=True,
                )
            else:
                finding_card(finding)
        st.caption(
            "Dose checks are arithmetic against general adult maxima printed on FDA labels. They "
            "are not personalised, and a prescriber may deliberately exceed a labelled maximum."
        )

    with tab6:
        if not timing:
            st.info("No specific food, alcohol or spacing rule is held for these medications.")
        for rule in timing:
            icon = {"food": "🍽️", "spacing": "⏱️", "avoid": "🚫"}.get(rule.get("rule_type"), "ℹ️")
            st.markdown(
                '<div class="mg-card" style="padding:12px 16px;margin-bottom:8px">'
                "<strong>%s %s</strong><br>"
                '<span style="font-size:0.9rem">%s</span><br>'
                '<span style="font-size:0.84rem;color:#5b6b7b">Why: %s</span><br>'
                '<span style="font-size:0.78rem;color:#5b6b7b">Source: %s</span></div>'
                % (icon, esc(rule["medication"]), esc(rule["guidance"]), esc(rule["reason"]),
                   esc(rule.get("source_name"))),
                unsafe_allow_html=True,
            )
        for conflict in findings.get("timing_conflicts") or []:
            st.warning("%s %s" % (conflict["description"], conflict.get("management") or ""))
        if timing:
            st.caption("Where this guidance and your own pharmacy label differ, follow the label "
                       "and ask the pharmacist.")

    with tab4:
        card("Pairs checked",
             "<p style='font-size:0.88rem'>%s</p>"
             % (esc("; ".join("%s + %s" % (a, b) for a, b in findings["checked_pairs"]))
                or "No pair could be checked."))
        card("Sources consulted",
             "".join("<p style='font-size:0.88rem'>• %s</p>" % esc(s)
                     for s in findings["sources_checked"]))
        card("Per-finding sources",
             "".join(
                 "<p style='font-size:0.88rem'>• %s%s</p>"
                 % (esc(s["name"]),
                    " — <a href='%s' target='_blank'>link</a>" % esc(s["url"]) if s.get("url") else "")
                 for s in report.get("sources", [])) or "<p>None.</p>")
        card("Information you still need to confirm",
             "".join("<p style='font-size:0.88rem'>• <strong>%s</strong>: %s not stated in the text</p>"
                     % (esc(m["medication"]), esc(", ".join(m["fields"])))
                     for m in report.get("missing_information", []))
             or "<p style='font-size:0.88rem'>Every field was present in the text.</p>")
        card("Limitations",
             "".join("<p style='font-size:0.88rem'>• %s</p>" % esc(limit)
                     for limit in findings["limitations"]))
        emergency_box()

    st.divider()
    st.subheader("Download the safety report")
    c1, c2, c3 = st.columns(3)
    if c1.button("Generate PDF", use_container_width=True):
        result = safety_report.export_pdf(report)
        path, kind = result if isinstance(result, tuple) else (result, "html")
        data = Path(path).read_bytes()
        if kind == "pdf":
            st.download_button("Download safety_report.pdf", data, file_name=Path(path).name,
                               mime="application/pdf", use_container_width=True)
        else:
            st.info("reportlab is not installed, so a print-ready HTML report was generated "
                    "instead (use your browser's Print → Save as PDF).")
            st.download_button("Download safety_report.html", data, file_name=Path(path).name,
                               mime="text/html", use_container_width=True)
    c2.download_button("Download Markdown", safety_report.to_markdown(report),
                       file_name="safety_report.md", use_container_width=True)
    c3.download_button("Download JSON", json.dumps(report, indent=2, default=str),
                       file_name="safety_report.json", use_container_width=True)
    st.caption("Report generated %s · data mode: %s"
               % (pretty_datetime(report["generated_at"]), report["data_mode"]))
    footer()


# --------------------------------------------------------------------------
# Re-open a stored report (used by Medication History)
# --------------------------------------------------------------------------
def load_stored_report(report_id: int) -> bool:
    stored = models.get_safety_report(report_id)
    if not stored:
        return False
    report = stored["report"]
    st.session_state.analysis = {
        "ok": True,
        "parse": {"parser": report.get("parser", {}), "items": report.get("parsed_items", []),
                  "errors": [], "warnings": []},
        "items": report.get("parsed_items", []),
        "findings": {
            "interactions": report.get("interactions", []),
            "allergy_warnings": report.get("allergy_warnings", []),
            "condition_warnings": report.get("condition_warnings", []),
            "checked_pairs": [tuple(p) for p in report.get("checked_pairs", [])],
            "unchecked_medications": report.get("unchecked_medications", []),
            "highest_severity": report.get("highest_severity", "none"),
            "counts": report.get("counts", {}),
            "limitations": report.get("limitations", []),
            "sources_checked": report.get("sources_checked", []),
        },
        "graph": None,
        "report": report,
        "prescription_id": report.get("prescription_id"),
        "report_id": report_id,
        "errors": [],
        "warnings": [],
    }
    return True
