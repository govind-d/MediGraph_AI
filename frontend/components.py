"""Shared UI pieces: theme CSS, cards, badges, disclaimers, graph rendering."""
from __future__ import annotations

import html

import streamlit as st

from utils.config import DISCLAIMER, EMERGENCY_GUIDANCE
from utils.helpers import SEVERITY_COLOR, SEVERITY_ICON, SEVERITY_LABEL, SEVERITY_TIER

THEME_CSS = """
<style>
  :root {
    --mg-primary: #15657f;
    --mg-accent: #2b8ca8;
    --mg-teal: #27a79b;
    --mg-ink: #1f2d3d;
    --mg-muted: #5b6b7b;
    --mg-line: #dce9ee;
    --mg-bg-soft: #f4f9fb;
  }
  .stApp { background: #ffffff; }
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f3fafc 0%, #ffffff 100%);
    border-right: 1px solid var(--mg-line);
  }
  h1, h2, h3 { color: var(--mg-primary); font-weight: 650; letter-spacing: -0.01em; }
  .mg-hero {
    background: linear-gradient(120deg, #15657f 0%, #2b8ca8 55%, #27a79b 100%);
    color: #fff; padding: 22px 26px; border-radius: 18px; margin-bottom: 18px;
    box-shadow: 0 10px 28px rgba(21,101,127,0.18);
  }
  .mg-hero h1 { color: #fff; margin: 0 0 4px 0; font-size: 1.6rem; }
  .mg-hero p { margin: 0; opacity: 0.92; font-size: 0.95rem; }
  .mg-card {
    background: #fff; border: 1px solid var(--mg-line); border-radius: 16px;
    padding: 18px 20px; margin-bottom: 14px; box-shadow: 0 2px 10px rgba(31,45,61,0.05);
  }
  .mg-card h4 { margin: 0 0 8px 0; color: var(--mg-primary); font-size: 1.02rem; }
  .mg-metric {
    background: var(--mg-bg-soft); border: 1px solid var(--mg-line); border-radius: 16px;
    padding: 16px 18px; height: 100%;
  }
  .mg-metric .label { color: var(--mg-muted); font-size: 0.78rem; text-transform: uppercase;
                      letter-spacing: 0.06em; margin-bottom: 6px; }
  .mg-metric .value { color: var(--mg-primary); font-size: 1.9rem; font-weight: 700; line-height: 1; }
  .mg-metric .hint { color: var(--mg-muted); font-size: 0.8rem; margin-top: 6px; }
  .mg-badge {
    display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 0.76rem;
    font-weight: 600; color: #fff; margin-right: 6px;
  }
  .mg-pill {
    display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 0.76rem;
    background: var(--mg-bg-soft); border: 1px solid var(--mg-line); color: var(--mg-muted);
    margin: 0 6px 6px 0;
  }
  .mg-finding { border-left: 5px solid var(--mg-accent); background: #fff;
                border: 1px solid var(--mg-line); border-radius: 14px;
                padding: 14px 18px; margin-bottom: 12px; }
  .mg-finding h4 { margin: 0 0 6px 0; font-size: 1rem; }
  .mg-finding p { margin: 4px 0; font-size: 0.9rem; color: var(--mg-ink); }
  .mg-finding .src { font-size: 0.78rem; color: var(--mg-muted); margin-top: 8px; }
  .mg-disclaimer {
    background: #fff8ea; border: 1px solid #f0d9ad; border-left: 5px solid #e08b2f;
    border-radius: 12px; padding: 12px 16px; font-size: 0.86rem; color: #6b4e18;
    margin-bottom: 14px;
  }
  .mg-emergency {
    background: #fdeeee; border: 1px solid #f3c7c7; border-left: 5px solid #d64545;
    border-radius: 12px; padding: 12px 16px; font-size: 0.86rem; color: #7d2525;
  }
  .mg-table { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
  .mg-table th { background: var(--mg-bg-soft); color: var(--mg-primary); text-align: left;
                 padding: 8px 10px; border-bottom: 1px solid var(--mg-line); }
  .mg-table td { padding: 8px 10px; border-bottom: 1px solid #eef5f8; vertical-align: top; }
  .mg-missing { color: #b4701f; font-style: italic; }
  .stButton > button {
    border-radius: 10px; border: 1px solid var(--mg-accent); background: var(--mg-accent);
    color: #fff; font-weight: 600; padding: 0.42rem 1.0rem;
  }
  .stButton > button:hover { background: var(--mg-primary); border-color: var(--mg-primary); }
  div[data-testid="stForm"] { border: 1px solid var(--mg-line); border-radius: 16px; padding: 16px; }
  .mg-foot { color: var(--mg-muted); font-size: 0.78rem; margin-top: 26px;
             border-top: 1px solid var(--mg-line); padding-top: 10px; }
  @media (max-width: 760px) {
    .mg-hero { padding: 16px; } .mg-metric .value { font-size: 1.5rem; }
  }
</style>
"""


def inject_css() -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def hero(title: str, subtitle: str = "") -> None:
    st.markdown(
        '<div class="mg-hero"><h1>%s</h1><p>%s</p></div>' % (esc(title), esc(subtitle)),
        unsafe_allow_html=True,
    )


def metric(label: str, value, hint: str = "") -> None:
    st.markdown(
        '<div class="mg-metric"><div class="label">%s</div><div class="value">%s</div>'
        '<div class="hint">%s</div></div>' % (esc(label), esc(value), esc(hint)),
        unsafe_allow_html=True,
    )


def severity_badge(severity: str, *, tier: bool = True) -> str:
    """Colour-coded badge, e.g. '🔴 High concern · Major'."""
    sev = str(severity or "none").lower()
    text = SEVERITY_LABEL.get(sev, sev)
    if tier and sev in ("high", "moderate", "informational"):
        text = "%s · %s" % (text, SEVERITY_TIER[sev])
    return '<span class="mg-badge" style="background:%s">%s %s</span>' % (
        SEVERITY_COLOR.get(sev, "#888"), SEVERITY_ICON.get(sev, ""), esc(text)
    )


def disclaimer_box() -> None:
    st.markdown('<div class="mg-disclaimer">⚕️ %s</div>'
                % DISCLAIMER.replace("**", ""), unsafe_allow_html=True)


def emergency_box() -> None:
    st.markdown('<div class="mg-emergency"><strong>Emergency guidance.</strong> %s</div>'
                % esc(EMERGENCY_GUIDANCE), unsafe_allow_html=True)


def card(title: str, body_html: str) -> None:
    st.markdown('<div class="mg-card"><h4>%s</h4>%s</div>' % (esc(title), body_html),
                unsafe_allow_html=True)


def finding_card(finding: dict) -> None:
    sev = str(finding.get("severity", "none")).lower()
    color = SEVERITY_COLOR.get(sev, "#888")
    if finding.get("type") == "allergy":
        heading = "%s — recorded allergy: %s" % (finding.get("medication"), finding.get("allergy"))
    elif finding.get("type") == "condition":
        heading = "%s — recorded condition: %s" % (finding.get("medication"), finding.get("condition"))
    elif finding.get("type") == "dose":
        heading = "%s — dosage check" % finding.get("medication")
    else:
        heading = "%s + %s" % (finding.get("drug_a"), finding.get("drug_b"))
    rows = []
    if finding.get("description"):
        rows.append("<p><strong>What it means:</strong> %s</p>" % esc(finding["description"]))
    if finding.get("mechanism"):
        rows.append("<p><strong>Why it happens:</strong> %s</p>" % esc(finding["mechanism"]))
    if finding.get("management"):
        rows.append("<p><strong>What is usually done:</strong> %s</p>" % esc(finding["management"]))
    if finding.get("evidence"):
        rows.append('<p class="src">Basis: %s</p>' % esc(finding["evidence"]))
    source = finding.get("source_name") or "not recorded"
    if finding.get("source_url"):
        source = '%s — <a href="%s" target="_blank">source</a>' % (esc(source), esc(finding["source_url"]))
    else:
        source = esc(source)
    st.markdown(
        '<div class="mg-finding" style="border-left-color:%s">%s<h4>%s</h4>%s'
        '<p class="src">Source: %s</p></div>'
        % (color, severity_badge(sev), esc(heading), "".join(rows), source),
        unsafe_allow_html=True,
    )


def parsed_table(items) -> None:
    """Render parsed medications, marking anything missing instead of guessing."""
    header = ["Medication", "Strength", "Dose", "Frequency", "Duration", "Route", "Instructions",
              "Confidence"]
    rows = []
    for item in items:
        cells = []
        for key in ("display_name", "strength", "dose", "frequency", "duration", "route",
                    "instructions"):
            value = item.get(key) or item.get("medication") if key == "display_name" else item.get(key)
            if value:
                cells.append(esc(value))
            else:
                cells.append('<span class="mg-missing">not stated — verify</span>')
        cells.append("%d%%" % round(100 * float(item.get("confidence") or 0)))
        rows.append("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in cells))
    st.markdown(
        '<table class="mg-table"><tr>%s</tr>%s</table>'
        % ("".join("<th>%s</th>" % h for h in header), "".join(rows)),
        unsafe_allow_html=True,
    )


def graph_figure(payload, height: int = 460):
    """Render the knowledge graph.

    Uses Plotly with a NetworkX spring layout when both are installed, and
    falls back to an SVG drawn on a circular layout otherwise.
    """
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    if not nodes:
        st.info("No graph to display yet.")
        return
    positions = _layout(nodes, edges)
    try:
        import plotly.graph_objects as go  # type: ignore

        edge_x, edge_y = [], []
        for edge in edges:
            if edge["source"] not in positions or edge["target"] not in positions:
                continue
            x0, y0 = positions[edge["source"]]
            x1, y1 = positions[edge["target"]]
            edge_x += [x0, x1, None]
            edge_y += [y0, y1, None]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines",
                                 line=dict(color="#c3d8e0", width=1.3), hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=[positions[n["id"]][0] for n in nodes if n["id"] in positions],
            y=[positions[n["id"]][1] for n in nodes if n["id"] in positions],
            mode="markers+text",
            text=[n["name"] for n in nodes if n["id"] in positions],
            textposition="top center",
            textfont=dict(size=11, color="#1f2d3d"),
            marker=dict(size=[26 if n["label"] == "Patient" else 18 for n in nodes if n["id"] in positions],
                        color=[n.get("color", "#2b8ca8") for n in nodes if n["id"] in positions],
                        line=dict(color="#ffffff", width=2)),
            hovertext=["%s: %s" % (n["label"], n["name"]) for n in nodes if n["id"] in positions],
            hoverinfo="text",
        ))
        xs = [positions[n["id"]][0] for n in nodes if n["id"] in positions] or [0]
        ys = [positions[n["id"]][1] for n in nodes if n["id"] in positions] or [0]
        padx = (max(xs) - min(xs)) * 0.22 + 0.15
        pady = (max(ys) - min(ys)) * 0.15 + 0.12
        fig.update_layout(showlegend=False, height=height, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis=dict(visible=False, range=[min(xs) - padx, max(xs) + padx]),
                          yaxis=dict(visible=False, range=[min(ys) - pady, max(ys) + pady]),
                          plot_bgcolor="#ffffff", paper_bgcolor="#ffffff")
        st.plotly_chart(fig, use_container_width=True)
        return
    except Exception:
        pass
    st.markdown(_svg_graph(nodes, edges, positions, height), unsafe_allow_html=True)


def _layout(nodes, edges):
    try:
        import networkx as nx  # type: ignore

        g = nx.Graph()
        for node in nodes:
            g.add_node(node["id"])
        for edge in edges:
            g.add_edge(edge["source"], edge["target"])
        pos = nx.spring_layout(g, seed=7, k=0.9)
        return {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
    except Exception:
        import math

        n = max(len(nodes), 1)
        out = {}
        for i, node in enumerate(nodes):
            if node["label"] == "Patient":
                out[node["id"]] = (0.0, 0.0)
            else:
                angle = 2 * math.pi * i / n
                out[node["id"]] = (math.cos(angle), math.sin(angle))
        return out


def _svg_graph(nodes, edges, positions, height):
    width, pad = 760, 70
    xs = [p[0] for p in positions.values()] or [0]
    ys = [p[1] for p in positions.values()] or [0]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

    def sx(x):
        return pad + (x - minx) / ((maxx - minx) or 1) * (width - 2 * pad)

    def sy(y):
        return pad + (y - miny) / ((maxy - miny) or 1) * (height - 2 * pad)

    parts = ['<svg viewBox="0 0 %d %d" width="100%%" style="background:#fff;border:1px solid '
             '#dce9ee;border-radius:14px">' % (width, height)]
    for edge in edges:
        if edge["source"] not in positions or edge["target"] not in positions:
            continue
        x1, y1 = positions[edge["source"]]
        x2, y2 = positions[edge["target"]]
        color = "#d64545" if edge.get("rel") == "CONTRAINDICATED_WITH" else "#c3d8e0"
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.4"/>'
                     % (sx(x1), sy(y1), sx(x2), sy(y2), color))
    for node in nodes:
        if node["id"] not in positions:
            continue
        x, y = positions[node["id"]]
        r = 13 if node["label"] == "Patient" else 9
        parts.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="%s" stroke="#fff" stroke-width="2"/>'
                     % (sx(x), sy(y), r, node.get("color", "#2b8ca8")))
        parts.append('<text x="%.1f" y="%.1f" font-size="11" fill="#1f2d3d" text-anchor="middle">%s</text>'
                     % (sx(x), sy(y) - r - 5, esc(node["name"])[:26]))
    parts.append("</svg>")
    return "".join(parts)


def source_mode_pill(text: str) -> None:
    st.markdown('<span class="mg-pill">%s</span>' % esc(text), unsafe_allow_html=True)


def footer() -> None:
    st.markdown(
        '<div class="mg-foot">MediGraph AI — NLP + knowledge graph + RAG prescription '
        'intelligence. Educational prototype: always confirm with a licensed clinician. '
        'Results marked “local dataset” are simulated lookups, not live clinical data.</div>',
        unsafe_allow_html=True,
    )


def require_patient(patient) -> bool:
    if patient:
        return True
    st.warning("Create or select a patient profile first (Patient Profiles in the sidebar).")
    return False
