"""Medical knowledge graph.

Entities      : Patient, Medication, Prescription, Condition, Allergy, Interaction
Relationships : PATIENT_TAKES, PRESCRIBED_IN, INTERACTS_WITH, CONTRAINDICATED_WITH,
                PATIENT_ALLERGIC_TO, PATIENT_HAS_CONDITION, BELONGS_TO_CLASS

Backends, chosen automatically:
  * Neo4j      - when USE_NEO4J=true and the `neo4j` driver can connect.
  * NetworkX   - lightweight in-process fallback (default).
  * Built-in   - a tiny dict-based graph if NetworkX is not installed, so the
                 application never loses graph functionality.

All backends build the same nodes and edges from the same records, so findings
do not depend on which one is active.
"""
from __future__ import annotations

from utils.config import Config
from utils.security import log_error

NODE_COLORS = {
    "Patient": "#1f6f8b",
    "Medication": "#2b8ca8",
    "Prescription": "#7aa8bf",
    "Condition": "#e08b2f",
    "Allergy": "#d64545",
    "Interaction": "#8e5bb5",
    "DrugClass": "#9fb7c4",
}


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class SimpleGraph:
    """Minimal dependency-free directed graph (fallback of last resort)."""

    backend_name = "built-in dict graph"

    def __init__(self):
        self._nodes: dict = {}
        self._edges: list = []

    def add_node(self, node_id, **attrs):
        self._nodes.setdefault(node_id, {}).update(attrs)

    def add_edge(self, src, dst, **attrs):
        self.add_node(src)
        self.add_node(dst)
        self._edges.append((src, dst, attrs))

    def nodes(self):
        return [(n, dict(a)) for n, a in self._nodes.items()]

    def edges(self):
        return [(s, d, dict(a)) for s, d, a in self._edges]

    def neighbors(self, node_id, rel=None):
        out = []
        for src, dst, attrs in self._edges:
            if src == node_id and (rel is None or attrs.get("rel") == rel):
                out.append(dst)
            elif dst == node_id and (rel is None or attrs.get("rel") == rel):
                out.append(src)
        return out


class NetworkXGraph(SimpleGraph):
    backend_name = "NetworkX"

    def __init__(self, nx_module):
        super().__init__()
        self.nx = nx_module
        self.g = nx_module.MultiDiGraph()

    def add_node(self, node_id, **attrs):
        super().add_node(node_id, **attrs)
        self.g.add_node(node_id, **attrs)

    def add_edge(self, src, dst, **attrs):
        super().add_edge(src, dst, **attrs)
        self.g.add_edge(src, dst, **attrs)

    def shortest_path(self, src, dst):
        try:
            return self.nx.shortest_path(self.g.to_undirected(as_view=False), src, dst)
        except Exception:
            return []

    def degree_centrality(self):
        try:
            return self.nx.degree_centrality(self.g)
        except Exception:  # pragma: no cover
            return {}


def _make_graph():
    try:
        import networkx as nx  # type: ignore

        return NetworkXGraph(nx)
    except Exception:
        return SimpleGraph()


class Neo4jStore:
    """Optional Neo4j persistence. Mirrors the same nodes and relationships."""

    backend_name = "Neo4j"

    def __init__(self):
        from neo4j import GraphDatabase  # type: ignore

        self.driver = GraphDatabase.driver(
            Config.NEO4J_URI, auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD)
        )
        self.driver.verify_connectivity()

    def close(self):
        try:
            self.driver.close()
        except Exception:  # pragma: no cover
            pass

    def write_graph(self, graph_payload):
        """Persist nodes/edges (idempotent MERGE statements)."""
        with self.driver.session() as session:
            for node in graph_payload["nodes"]:
                session.run(
                    "MERGE (n:%s {key:$key}) SET n += $props" % node["label"],
                    key=node["id"], props={k: v for k, v in node.items()
                                           if k not in ("id", "label") and v is not None},
                )
            for edge in graph_payload["edges"]:
                session.run(
                    "MATCH (a {key:$src}), (b {key:$dst}) "
                    "MERGE (a)-[r:%s]->(b) SET r += $props" % edge["rel"],
                    src=edge["source"], dst=edge["target"],
                    props={k: v for k, v in edge.items()
                           if k not in ("source", "target", "rel") and v is not None},
                )

    def interactions_for_patient(self, patient_key):
        cypher = (
            "MATCH (p:Patient {key:$key})-[:PATIENT_TAKES]->(m1:Medication)"
            "-[r:INTERACTS_WITH]-(m2:Medication)<-[:PATIENT_TAKES]-(p) "
            "RETURN m1.name AS a, m2.name AS b, r.severity AS severity"
        )
        with self.driver.session() as session:
            return [dict(rec) for rec in session.run(cypher, key=patient_key)]


_NEO4J_STORE = None
_NEO4J_STATUS = "not attempted"


def neo4j_store():
    """Return a connected Neo4jStore, or None (reason available via neo4j_status)."""
    global _NEO4J_STORE, _NEO4J_STATUS
    if _NEO4J_STORE is not None:
        return _NEO4J_STORE
    if not Config.USE_NEO4J:
        _NEO4J_STATUS = "disabled (USE_NEO4J is false) - using the local fallback graph"
        return None
    try:
        _NEO4J_STORE = Neo4jStore()
        _NEO4J_STATUS = "connected to %s" % Config.NEO4J_URI
    except Exception as exc:
        log_error("Neo4j connection", exc)
        _NEO4J_STATUS = "unavailable (%s) - using the local fallback graph" % type(exc).__name__
        _NEO4J_STORE = None
    return _NEO4J_STORE


def neo4j_status() -> str:
    neo4j_store()
    return _NEO4J_STATUS


# --------------------------------------------------------------------------
# Graph construction
# --------------------------------------------------------------------------
def build_graph(patient, items, *, allergies=None, conditions=None, findings=None,
                prescription_id=None):
    """Build the knowledge graph for one patient and prescription.

    Returns {"graph": <backend>, "payload": {"nodes": [...], "edges": [...]},
             "backend": str, "stats": {...}}
    """
    allergies = allergies or []
    conditions = conditions or []
    findings = findings or {}
    graph = _make_graph()

    patient_key = "patient:%s" % (patient.get("id") if patient else "unknown")
    patient_name = (patient or {}).get("name", "Patient")
    graph.add_node(patient_key, label="Patient", name=patient_name,
                   age=(patient or {}).get("age"), title="Patient profile")

    rx_key = None
    if prescription_id:
        rx_key = "prescription:%s" % prescription_id
        graph.add_node(rx_key, label="Prescription", name="Prescription #%s" % prescription_id)
        graph.add_edge(patient_key, rx_key, rel="HAS_PRESCRIPTION")

    for item in items:
        name = item.get("display_name") or item.get("medication") or "Unknown"
        key = "medication:%s" % (item.get("normalized_name") or name.lower())
        graph.add_node(
            key, label="Medication", name=name,
            drug_class=item.get("drug_class"), rxcui=item.get("rxcui"),
            strength=item.get("strength"), dose=item.get("dose"),
            frequency=item.get("frequency"), duration=item.get("duration"),
            recognized=item.get("recognized", False),
        )
        graph.add_edge(patient_key, key, rel="PATIENT_TAKES",
                       dose=item.get("dose"), frequency=item.get("frequency"))
        if rx_key:
            graph.add_edge(key, rx_key, rel="PRESCRIBED_IN")
        if item.get("drug_class"):
            class_key = "class:%s" % str(item["drug_class"]).lower()
            graph.add_node(class_key, label="DrugClass", name=item["drug_class"],
                           kind="drug class")
            graph.add_edge(key, class_key, rel="BELONGS_TO_CLASS")

    for allergy in allergies:
        substance = str(allergy.get("substance") or "").strip()
        if not substance:
            continue
        key = "allergy:%s" % substance.lower()
        graph.add_node(key, label="Allergy", name=substance,
                       reaction=allergy.get("reaction"), severity=allergy.get("severity"))
        graph.add_edge(patient_key, key, rel="PATIENT_ALLERGIC_TO")

    for condition in conditions:
        name = str(condition.get("condition_name") or "").strip()
        if not name:
            continue
        key = "condition:%s" % name.lower()
        graph.add_node(key, label="Condition", name=name, status=condition.get("status"))
        graph.add_edge(patient_key, key, rel="PATIENT_HAS_CONDITION")

    # Interaction nodes link the two medications involved
    for i, finding in enumerate(findings.get("interactions", [])):
        key = "interaction:%d" % i
        graph.add_node(key, label="Interaction", name="%s + %s" % (finding["drug_a"], finding["drug_b"]),
                       severity=finding["severity"], description=finding.get("description"),
                       source=finding.get("source_name"))
        a_key = _find_medication_key(graph, finding["drug_a"])
        b_key = _find_medication_key(graph, finding["drug_b"])
        rel = "CONTRAINDICATED_WITH" if finding["severity"] == "high" else "INTERACTS_WITH"
        if a_key and b_key:
            graph.add_edge(a_key, b_key, rel=rel, severity=finding["severity"],
                           description=finding.get("description"))
            graph.add_edge(a_key, key, rel="HAS_INTERACTION")
            graph.add_edge(b_key, key, rel="HAS_INTERACTION")

    for finding in findings.get("allergy_warnings", []):
        med_key = _find_medication_key(graph, finding.get("medication"))
        allergy_key = "allergy:%s" % str(finding.get("allergy") or "").lower()
        if med_key and allergy_key in dict(graph.nodes()):
            graph.add_edge(med_key, allergy_key, rel="CONTRAINDICATED_WITH",
                           severity=finding.get("severity"))

    for finding in findings.get("condition_warnings", []):
        med_key = _find_medication_key(graph, finding.get("medication"))
        cond_key = "condition:%s" % str(finding.get("condition") or "").lower()
        if med_key:
            if cond_key not in dict(graph.nodes()):
                graph.add_node(cond_key, label="Condition", name=finding.get("condition"))
                graph.add_edge(patient_key, cond_key, rel="PATIENT_HAS_CONDITION")
            graph.add_edge(med_key, cond_key, rel="CAUTION_WITH",
                           severity=finding.get("severity"))

    payload = to_payload(graph)
    backend = graph.backend_name
    store = neo4j_store()
    if store:
        try:
            store.write_graph(payload)
            backend = "Neo4j (+ local mirror)"
        except Exception as exc:
            log_error("Neo4j write", exc)
            backend = "%s (Neo4j write failed)" % graph.backend_name
    return {
        "graph": graph,
        "payload": payload,
        "backend": backend,
        "patient_key": patient_key,
        "stats": {
            "nodes": len(payload["nodes"]),
            "edges": len(payload["edges"]),
            "medications": sum(1 for n in payload["nodes"] if n["label"] == "Medication"),
            "interactions": sum(1 for n in payload["nodes"] if n["label"] == "Interaction"),
        },
    }


def _find_medication_key(graph, display_name):
    if not display_name:
        return None
    target = str(display_name).strip().lower()
    for node_id, attrs in graph.nodes():
        if attrs.get("label") == "Medication" and str(attrs.get("name", "")).lower() == target:
            return node_id
    for node_id, attrs in graph.nodes():
        if attrs.get("label") == "Medication" and target in str(attrs.get("name", "")).lower():
            return node_id
    return None


def to_payload(graph) -> dict:
    nodes = [
        {"id": node_id, "label": attrs.get("label", "Node"),
         "name": attrs.get("name", node_id),
         "color": NODE_COLORS.get(attrs.get("label"), "#888"),
         **{k: v for k, v in attrs.items() if k not in ("label", "name")}}
        for node_id, attrs in graph.nodes()
    ]
    edges = [
        {"source": src, "target": dst, "rel": attrs.get("rel", "RELATED"),
         **{k: v for k, v in attrs.items() if k != "rel"}}
        for src, dst, attrs in graph.edges()
    ]
    return {"nodes": nodes, "edges": edges}


def describe_graph(payload) -> list[str]:
    """Readable sentences describing each relationship (used by the RAG corpus)."""
    names = {n["id"]: n["name"] for n in payload["nodes"]}
    out = []
    verbs = {
        "PATIENT_TAKES": "is taking",
        "PATIENT_ALLERGIC_TO": "has a recorded allergy to",
        "PATIENT_HAS_CONDITION": "has the recorded condition",
        "HAS_PRESCRIPTION": "has",
        "PRESCRIBED_IN": "was prescribed in",
        "INTERACTS_WITH": "interacts with",
        "CONTRAINDICATED_WITH": "is flagged as contraindicated with",
        "CAUTION_WITH": "needs caution because of",
        "BELONGS_TO_CLASS": "belongs to the class",
        "HAS_INTERACTION": "is involved in the interaction",
    }
    for edge in payload["edges"]:
        src = names.get(edge["source"], edge["source"])
        dst = names.get(edge["target"], edge["target"])
        verb = verbs.get(edge["rel"], edge["rel"].lower().replace("_", " "))
        out.append("%s %s %s." % (src, verb, dst))
    return out


def cypher_script(payload) -> str:
    """Export the graph as a Cypher script for manual loading into Neo4j."""
    lines = ["// MediGraph AI graph export", "// Run with: cat graph.cypher | cypher-shell -u neo4j -p <password>"]
    for node in payload["nodes"]:
        props = ", ".join(
            "%s: %r" % (k, v) for k, v in node.items()
            if k not in ("id", "label", "color") and v is not None and not isinstance(v, (list, dict))
        )
        lines.append("MERGE (:%s {key: %r%s});" % (node["label"], node["id"],
                                                   (", " + props) if props else ""))
    for edge in payload["edges"]:
        lines.append(
            "MATCH (a {key: %r}), (b {key: %r}) MERGE (a)-[:%s]->(b);"
            % (edge["source"], edge["target"], edge["rel"])
        )
    return "\n".join(lines)
