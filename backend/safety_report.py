"""Medication safety report: assembly, Markdown rendering and PDF export.

PDF generation uses reportlab when installed. Without it, a self-contained,
print-ready HTML file is produced instead (the browser's "Save as PDF" then
gives the same document), so the export feature always works.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from utils.config import DISCLAIMER, EMERGENCY_GUIDANCE, EXPORT_DIR
from utils.helpers import SEVERITY_LABEL, now_iso, pretty_datetime
from utils.security import log_error


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def build_report(*, patient, items, findings, raw_text="", prescription_id=None,
                 allergies=None, conditions=None, parser_info=None, graph_info=None) -> dict:
    allergies = allergies or []
    conditions = conditions or []
    missing = []
    for item in items:
        if item.get("needs_verification"):
            missing.append({
                "medication": item.get("display_name") or item.get("medication"),
                "fields": item["needs_verification"],
            })
    unrecognised = [i.get("medication") for i in items if not i.get("normalized_name")]

    sources = []
    for finding in (findings.get("interactions", []) + findings.get("allergy_warnings", [])
                    + findings.get("condition_warnings", [])):
        entry = {"name": finding.get("source_name"), "url": finding.get("source_url")}
        if entry["name"] and entry not in sources:
            sources.append(entry)
    for item in items:
        name = item.get("normalization_source")
        if name:
            entry = {"name": "Medication identification: %s" % name,
                     "url": item.get("normalization_source_url")}
            if entry not in sources:
                sources.append(entry)

    report = {
        "generated_at": now_iso(),
        "prescription_id": prescription_id,
        "patient": {
            "name": (patient or {}).get("name"),
            "relationship": (patient or {}).get("relationship"),
            "age": (patient or {}).get("age"),
            "sex": (patient or {}).get("sex"),
            "is_pregnant": bool((patient or {}).get("is_pregnant")),
            "allergies": [
                {"substance": a.get("substance"), "reaction": a.get("reaction"),
                 "severity": a.get("severity")} for a in allergies
            ],
            "conditions": [
                {"name": c.get("condition_name"), "status": c.get("status")} for c in conditions
            ],
        },
        "raw_text": raw_text,
        "parsed_items": items,
        "medications": [
            {
                "name": i.get("display_name") or i.get("medication"),
                "normalized_name": i.get("normalized_name"),
                "rxcui": i.get("rxcui"),
                "drug_class": i.get("drug_class"),
                "strength": i.get("strength"),
                "dose": i.get("dose"),
                "frequency": i.get("frequency"),
                "duration": i.get("duration"),
                "route": i.get("route"),
                "instructions": i.get("instructions"),
                "identification_source": i.get("normalization_source"),
                "verified_external": i.get("verified_external", False),
            }
            for i in items
        ],
        "interactions": findings.get("interactions", []),
        "allergy_warnings": findings.get("allergy_warnings", []),
        "condition_warnings": findings.get("condition_warnings", []),
        "dose_warnings": findings.get("dose_warnings", []),
        "timing_guidance": findings.get("timing_guidance", []),
        "timing_conflicts": findings.get("timing_conflicts", []),
        "checked_pairs": findings.get("checked_pairs", []),
        "unchecked_medications": findings.get("unchecked_medications", []) or unrecognised,
        "highest_severity": findings.get("highest_severity", "none"),
        "counts": findings.get("counts", {}),
        "missing_information": missing,
        "limitations": findings.get("limitations", []),
        "sources_checked": findings.get("sources_checked", []),
        "sources": sources,
        "parser": parser_info or {},
        "graph": graph_info or {},
        "disclaimer": DISCLAIMER,
        "emergency_guidance": EMERGENCY_GUIDANCE,
        "data_mode": "Simulated / local dataset results unless a finding's source names a live API",
    }
    report["summary"] = _summary_sentence(report)
    return report


def _summary_sentence(report) -> str:
    counts = report.get("counts", {})
    total = sum(counts.get(k, 0) for k in ("high", "moderate", "informational"))
    meds = len(report.get("medications", []))
    if total == 0:
        return (
            "No known interaction was found between the %d medication(s) checked, in the sources "
            "this tool consulted. That is not the same as a guarantee of safety - see the "
            "limitations section." % meds
        )
    parts = []
    for key in ("high", "moderate", "informational"):
        if counts.get(key):
            parts.append("%d %s" % (counts[key], SEVERITY_LABEL[key].lower()))
    return "%d medication(s) checked. Findings: %s. Discuss these with a pharmacist or prescriber." % (
        meds, ", ".join(parts)
    )


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def to_markdown(report) -> str:
    patient = report.get("patient", {})
    lines = [
        "# MediGraph AI - Medication Safety Report",
        "",
        "_Generated %s_" % pretty_datetime(report.get("generated_at")),
        "",
        "> %s" % report.get("disclaimer", ""),
        "",
        "## 1. Patient profile used for this check",
        "",
        "- **Name:** %s" % (patient.get("name") or "-"),
        "- **Relationship:** %s" % (patient.get("relationship") or "-"),
        "- **Age:** %s" % (patient.get("age") if patient.get("age") is not None else "not recorded"),
        "- **Sex:** %s" % (patient.get("sex") or "not recorded"),
        "- **Pregnancy recorded:** %s" % ("yes" if patient.get("is_pregnant") else "no / not recorded"),
        "- **Recorded allergies:** %s" % (
            ", ".join("%s%s" % (a["substance"], " (%s)" % a["reaction"] if a.get("reaction") else "")
                      for a in patient.get("allergies", [])) or "none recorded"
        ),
        "- **Recorded conditions:** %s" % (
            ", ".join(c["name"] for c in patient.get("conditions", [])) or "none recorded"
        ),
        "",
        "## 2. Prescription as entered",
        "",
        "```",
        (report.get("raw_text") or "").strip() or "(not stored)",
        "```",
        "",
        "## 3. Parsed prescription details",
        "",
        "| Medication | Strength | Dose | Frequency | Duration | Route | Instructions |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for med in report.get("medications", []):
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            med.get("name") or "-", med.get("strength") or "_not stated_",
            med.get("dose") or "_not stated_", med.get("frequency") or "_not stated_",
            med.get("duration") or "_not stated_", med.get("route") or "_not stated_",
            med.get("instructions") or "-",
        ))
    lines += ["", "## 4. Medications identified", ""]
    for med in report.get("medications", []):
        lines.append("- **%s** - class: %s; RxNorm RXCUI: %s; identified via: %s" % (
            med.get("name"), med.get("drug_class") or "not recorded",
            med.get("rxcui") or "not available", med.get("identification_source") or "unknown",
        ))
    if report.get("unchecked_medications"):
        lines += ["", "> **Not checked for interactions:** %s. These names were not found in the "
                      "drug dataset, so no interaction result - positive or negative - applies to them."
                  % ", ".join(str(m) for m in report["unchecked_medications"])]

    lines += ["", "## 5. Potential drug interactions", ""]
    if not report.get("interactions"):
        lines.append("No known interaction was found between the medications checked, "
                     "**in the sources consulted**. See section 10 for what that does and does not mean.")
    for finding in report["interactions"]:
        lines += [
            "### %s - %s + %s" % (SEVERITY_LABEL.get(finding["severity"], finding["severity"]),
                                  finding["drug_a"], finding["drug_b"]),
            "",
            "- **What it means:** %s" % finding.get("description"),
            "- **Why it happens:** %s" % (finding.get("mechanism") or "not recorded"),
            "- **What is usually done:** %s" % (finding.get("management") or "discuss with a clinician"),
            "- **Source:** %s%s" % (finding.get("source_name") or "n/a",
                                    " (%s)" % finding["source_url"] if finding.get("source_url") else ""),
            "",
        ]

    lines += ["## 6. Allergy-related warnings", ""]
    if not report.get("allergy_warnings"):
        lines.append("No conflict found between the recorded allergies and the prescribed medications.")
    for finding in report["allergy_warnings"]:
        lines += [
            "### %s - %s vs allergy '%s'" % (SEVERITY_LABEL.get(finding["severity"], finding["severity"]),
                                             finding.get("medication"), finding.get("allergy")),
            "",
            "- %s" % finding.get("description"),
            "- **What is usually done:** %s" % finding.get("management"),
            "- **Basis:** %s" % finding.get("evidence"),
            "- **Source:** %s" % (finding.get("source_name") or "n/a"),
            "",
        ]

    lines += ["## 7. Condition-related warnings", ""]
    if not report.get("condition_warnings"):
        lines.append("No condition-specific caution was found for the recorded conditions.")
    for finding in report["condition_warnings"]:
        lines += [
            "### %s - %s with '%s'" % (SEVERITY_LABEL.get(finding["severity"], finding["severity"]),
                                       finding.get("medication"), finding.get("condition")),
            "",
            "- %s" % finding.get("description"),
            "- **What is usually done:** %s" % finding.get("management"),
            "- **Source:** %s" % (finding.get("source_name") or "n/a"),
            "",
        ]

    lines += ["## 8. Dosage check", ""]
    dose_warnings = report.get("dose_warnings") or []
    if not dose_warnings:
        lines.append("No dose could be totalled from the information given.")
    for finding in dose_warnings:
        lines += [
            "### %s - %s" % (finding.get("medication"),
                             "Within labelled maximum" if finding["severity"] == "none"
                             else SEVERITY_LABEL.get(finding["severity"], finding["severity"])),
            "",
            "- %s" % finding.get("description"),
            "- **Working:** %s" % (finding.get("evidence") or "-"),
            "- **What is usually done:** %s" % (finding.get("management") or "-"),
            "- **Source:** %s" % (finding.get("source_name") or "n/a"),
            "",
        ]

    lines += ["## 9. Food, alcohol and timing guidance", ""]
    timing = report.get("timing_guidance") or []
    if not timing:
        lines.append("No specific food or timing rule is held for these medications.")
    for rule in timing:
        lines.append("- **%s** (%s): %s _Why:_ %s %s"
                     % (rule.get("medication"), rule.get("rule_type"), rule.get("guidance"),
                        rule.get("reason"),
                        "(Source: %s)" % rule.get("source_name") if rule.get("source_name") else ""))
    for conflict in report.get("timing_conflicts") or []:
        lines.append("- ⏱ **Timing to arrange:** %s %s"
                     % (conflict.get("description"), conflict.get("management") or ""))
    lines.append("")
    lines.append("Where this guidance and your own pharmacy label differ, follow the label and "
                 "ask the pharmacist.")
    lines.append("")

    lines += ["## 10. Information sources used", ""]
    for source in report.get("sources_checked", []):
        lines.append("- %s" % source)
    for source in report.get("sources", []):
        lines.append("- %s%s" % (source["name"], " - %s" % source["url"] if source.get("url") else ""))

    lines += ["", "## 11. Information to confirm", ""]
    if not report.get("missing_information"):
        lines.append("Every field this tool looks for was present in the prescription text.")
    for entry in report["missing_information"]:
        lines.append("- **%s**: %s not stated in the text - confirm against the written "
                     "prescription or pharmacy label." % (entry["medication"], ", ".join(entry["fields"])))

    lines += ["", "## 12. Limitations of this check", ""]
    for limitation in report.get("limitations", []):
        lines.append("- %s" % limitation)

    lines += [
        "",
        "## 13. Emergency guidance",
        "",
        report.get("emergency_guidance", ""),
        "",
        "---",
        "",
        "_%s_" % report.get("disclaimer", ""),
        "",
        "_Data mode: %s_" % report.get("data_mode", ""),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def _safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text))[:60] or "report"


def export_markdown(report, filename=None) -> Path:
    name = filename or "safety_report_%s_%s.md" % (
        _safe_filename(report.get("patient", {}).get("name") or "patient"),
        _safe_filename(report.get("generated_at", "")).replace(":", ""),
    )
    path = EXPORT_DIR / name
    path.write_text(to_markdown(report), encoding="utf-8")
    return path


def export_json(report, filename=None) -> Path:
    name = filename or "safety_report_%s.json" % _safe_filename(report.get("generated_at", ""))
    path = EXPORT_DIR / name
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def _markdown_to_html(markdown_text: str) -> str:
    out, in_table, in_code = [], False, False
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.strip() == "```":
            in_code = not in_code
            out.append("<pre>" if in_code else "</pre>")
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        if line.startswith("| "):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("- :"):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>%s</tr>" % "".join("<th>%s</th>" % _inline(c) for c in cells))
                continue
            out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in cells))
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if line.startswith("### "):
            out.append("<h3>%s</h3>" % _inline(line[4:]))
        elif line.startswith("## "):
            out.append("<h2>%s</h2>" % _inline(line[3:]))
        elif line.startswith("# "):
            out.append("<h1>%s</h1>" % _inline(line[2:]))
        elif line.startswith("> "):
            out.append('<blockquote>%s</blockquote>' % _inline(line[2:]))
        elif line.startswith("- "):
            out.append("<li>%s</li>" % _inline(line[2:]))
        elif line.startswith("---"):
            out.append("<hr>")
        elif line.strip():
            out.append("<p>%s</p>" % _inline(line))
    if in_table:
        out.append("</table>")
    body = "\n".join(out)
    body = body.replace("<li>", "<ul><li>").replace("</li>", "</li></ul>")
    return body


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"_(.+?)_", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(https?://[^\s)<]+)", r'<a href="\1">\1</a>', escaped)
    return escaped


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>MediGraph AI - Medication Safety Report</title>
<style>
  @page { size: A4; margin: 18mm; }
  body { font-family: "Segoe UI", Arial, sans-serif; color: #1f2d3d; line-height: 1.5;
         max-width: 880px; margin: 0 auto; padding: 24px; }
  h1 { color: #15657f; border-bottom: 3px solid #2b8ca8; padding-bottom: 8px; }
  h2 { color: #15657f; margin-top: 28px; border-bottom: 1px solid #d7e6ec; padding-bottom: 4px; }
  h3 { color: #1f2d3d; margin-bottom: 4px; }
  table { border-collapse: collapse; width: 100%%; margin: 12px 0; font-size: 13px; }
  th, td { border: 1px solid #cfe0e7; padding: 6px 8px; text-align: left; vertical-align: top; }
  th { background: #eaf4f8; }
  blockquote { background: #fff7e6; border-left: 4px solid #e08b2f; margin: 12px 0;
               padding: 10px 14px; border-radius: 6px; }
  pre { background: #f4f8fa; border: 1px solid #dce9ee; padding: 10px; border-radius: 6px;
        white-space: pre-wrap; font-size: 13px; }
  ul { margin: 6px 0 6px 18px; }
  a { color: #15657f; }
  .footer { margin-top: 32px; font-size: 12px; color: #5b6b7b; border-top: 1px solid #d7e6ec;
            padding-top: 10px; }
</style></head><body>
%s
<div class="footer">MediGraph AI - educational decision support. Not a substitute for a doctor or pharmacist.</div>
</body></html>
"""


def export_html(report, filename=None) -> Path:
    name = filename or "safety_report_%s.html" % _safe_filename(
        (report.get("generated_at") or "").replace(":", "")
    )
    path = EXPORT_DIR / name
    path.write_text(_HTML_TEMPLATE % _markdown_to_html(to_markdown(report)), encoding="utf-8")
    return path


def export_pdf(report, filename=None):
    """Export the report as PDF.

    Returns (path, kind) where kind is "pdf" (reportlab) or "html"
    (print-ready fallback when reportlab is not installed).
    """
    name = filename or "safety_report_%s_%s.pdf" % (
        _safe_filename(report.get("patient", {}).get("name") or "patient"),
        _safe_filename((report.get("generated_at") or "").replace(":", "")),
    )
    try:
        from reportlab.lib import colors  # type: ignore
        from reportlab.lib.enums import TA_LEFT  # type: ignore
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
        from reportlab.lib.units import mm  # type: ignore
        from reportlab.platypus import (  # type: ignore
            HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table,
            TableStyle,
        )
    except Exception as exc:
        log_error("reportlab import", exc)
        return export_html(report), "html"

    path = EXPORT_DIR / name
    styles = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9.5, leading=13,
                          alignment=TA_LEFT)
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=17, textColor=colors.HexColor("#15657f"))
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=12,
                        textColor=colors.HexColor("#15657f"))
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=10.5, spaceBefore=6)
    note = ParagraphStyle("Note", parent=body, fontSize=8.5, textColor=colors.HexColor("#5b6b7b"))

    def md_to_rl(text):
        text = html.escape(str(text or ""))
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
        return text

    story = [Paragraph("MediGraph AI - Medication Safety Report", h1),
             Paragraph("Generated %s" % pretty_datetime(report.get("generated_at")), note),
             Spacer(1, 6),
             Paragraph(md_to_rl(report.get("disclaimer")), note),
             HRFlowable(width="100%", color=colors.HexColor("#2b8ca8")), Spacer(1, 8),
             Paragraph(md_to_rl(report.get("summary")), body), Spacer(1, 6)]

    patient = report.get("patient", {})
    story.append(Paragraph("1. Patient profile used for this check", h2))
    rows = [
        ["Name", patient.get("name") or "-"],
        ["Relationship", patient.get("relationship") or "-"],
        ["Age", str(patient.get("age")) if patient.get("age") is not None else "not recorded"],
        ["Sex", patient.get("sex") or "not recorded"],
        ["Pregnancy recorded", "yes" if patient.get("is_pregnant") else "no / not recorded"],
        ["Allergies", ", ".join(a["substance"] for a in patient.get("allergies", [])) or "none recorded"],
        ["Conditions", ", ".join(c["name"] for c in patient.get("conditions", [])) or "none recorded"],
    ]
    table = Table([[Paragraph(r[0], body), Paragraph(md_to_rl(r[1]), body)] for r in rows],
                  colWidths=[38 * mm, 128 * mm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfe0e7")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf4f8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [table, Spacer(1, 6)]

    story.append(Paragraph("2. Prescription as entered", h2))
    story.append(Paragraph(md_to_rl((report.get("raw_text") or "(not stored)").strip()).replace("\n", "<br/>"), body))

    story.append(Paragraph("3. Parsed prescription details", h2))
    head = ["Medication", "Strength", "Dose", "Frequency", "Duration", "Route"]
    data = [[Paragraph("<b>%s</b>" % h, body) for h in head]]
    for med in report.get("medications", []):
        data.append([Paragraph(md_to_rl(med.get(k) or "not stated"), body)
                     for k in ("name", "strength", "dose", "frequency", "duration", "route")])
    med_table = Table(data, colWidths=[36 * mm, 22 * mm, 24 * mm, 36 * mm, 24 * mm, 24 * mm])
    med_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfe0e7")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf4f8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [med_table, Spacer(1, 4)]
    if report.get("unchecked_medications"):
        story.append(Paragraph(
            "Not checked for interactions: %s. No interaction result applies to these."
            % ", ".join(str(m) for m in report["unchecked_medications"]), note))

    def findings_section(title, findings, empty_text, formatter):
        story.append(Paragraph(title, h2))
        if not findings:
            story.append(Paragraph(empty_text, body))
            return
        for finding in findings:
            story.append(Paragraph(formatter(finding), h3))
            for label, key in (("What it means", "description"), ("Why it happens", "mechanism"),
                               ("What is usually done", "management"), ("Source", "source_name")):
                if finding.get(key):
                    story.append(Paragraph("<b>%s:</b> %s" % (label, md_to_rl(finding[key])), body))
            if finding.get("source_url"):
                story.append(Paragraph(md_to_rl(finding["source_url"]), note))
            story.append(Spacer(1, 4))

    findings_section(
        "4. Potential drug interactions", report.get("interactions"),
        "No known interaction was found between the medications checked, in the sources consulted.",
        lambda f: "%s - %s + %s" % (SEVERITY_LABEL.get(f["severity"], f["severity"]),
                                    f["drug_a"], f["drug_b"]),
    )
    findings_section(
        "5. Allergy-related warnings", report.get("allergy_warnings"),
        "No conflict found between the recorded allergies and the prescribed medications.",
        lambda f: "%s - %s vs allergy '%s'" % (SEVERITY_LABEL.get(f["severity"], f["severity"]),
                                               f.get("medication"), f.get("allergy")),
    )
    findings_section(
        "6. Condition-related warnings", report.get("condition_warnings"),
        "No condition-specific caution was found for the recorded conditions.",
        lambda f: "%s - %s with '%s'" % (SEVERITY_LABEL.get(f["severity"], f["severity"]),
                                         f.get("medication"), f.get("condition")),
    )

    findings_section(
        "7. Dosage check", report.get("dose_warnings"),
        "No dose could be totalled from the information given.",
        lambda f: "%s - %s" % (f.get("medication"),
                               "Within labelled maximum" if f["severity"] == "none"
                               else SEVERITY_LABEL.get(f["severity"], f["severity"])),
    )

    story.append(Paragraph("8. Food, alcohol and timing guidance", h2))
    timing = report.get("timing_guidance") or []
    if timing:
        story.append(ListFlowable(
            [ListItem(Paragraph("<b>%s</b> (%s): %s <i>%s</i>"
                                % (md_to_rl(r.get("medication")), md_to_rl(r.get("rule_type")),
                                   md_to_rl(r.get("guidance")), md_to_rl(r.get("reason"))), body))
             for r in timing], bulletType="bullet"))
    else:
        story.append(Paragraph("No specific food or timing rule is held for these medications.", body))
    for conflict in report.get("timing_conflicts") or []:
        story.append(Paragraph("<b>Timing to arrange:</b> %s %s"
                               % (md_to_rl(conflict.get("description")),
                                  md_to_rl(conflict.get("management") or "")), body))
    story.append(Paragraph("Where this guidance and your own pharmacy label differ, follow the "
                           "label and ask the pharmacist.", note))

    story.append(Paragraph("9. Information to confirm", h2))
    if report.get("missing_information"):
        story.append(ListFlowable(
            [ListItem(Paragraph("<b>%s</b>: %s not stated in the text."
                                % (md_to_rl(m["medication"]), ", ".join(m["fields"])), body))
             for m in report["missing_information"]], bulletType="bullet"))
    else:
        story.append(Paragraph("Every field this tool looks for was present in the prescription text.", body))

    story.append(Paragraph("10. Information sources used", h2))
    all_sources = [s for s in report.get("sources_checked", [])]
    all_sources += ["%s%s" % (s["name"], " - %s" % s["url"] if s.get("url") else "")
                    for s in report.get("sources", [])]
    story.append(ListFlowable([ListItem(Paragraph(md_to_rl(s), body)) for s in all_sources] or
                              [ListItem(Paragraph("None recorded.", body))], bulletType="bullet"))

    story.append(Paragraph("11. Limitations of this check", h2))
    story.append(ListFlowable([ListItem(Paragraph(md_to_rl(limit), body))
                               for limit in report.get("limitations", [])], bulletType="bullet"))

    story.append(Paragraph("12. Emergency guidance", h2))
    story.append(Paragraph(md_to_rl(report.get("emergency_guidance")), body))
    story += [Spacer(1, 10), HRFlowable(width="100%", color=colors.HexColor("#d7e6ec")),
              Paragraph(md_to_rl(report.get("disclaimer")), note),
              Paragraph("Data mode: %s" % md_to_rl(report.get("data_mode")), note)]

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="MediGraph AI Medication Safety Report")
    doc.build(story)
    return path, "pdf"
