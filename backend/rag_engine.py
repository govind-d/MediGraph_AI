"""Retrieval-augmented chatbot engine.

Corpus (built per question, so answers always reflect current records):
  * `data/medical_knowledge.json` - curated reference library with sources.
  * Medication records for the patient's own prescriptions.
  * The patient's latest safety-report findings (interactions, allergies,
    conditions), including the source of each finding.
  * Prescription instructions as parsed, so dosage questions are answered from
    what was actually written.

Retrieval: TF-IDF cosine similarity implemented in pure Python (no mandatory
dependency). When `faiss`/`chromadb` plus `sentence-transformers` are
installed, dense embedding retrieval is used instead and reported in the UI.

Generation: when an LLM key is configured the retrieved passages are passed to
the model under a strict system prompt. Without a key the engine composes an
extractive answer from the retrieved passages. Either way, the answer may only
use retrieved text, every answer carries its sources, and the engine refuses to
diagnose or to prescribe.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter

from utils.config import DATA_DIR, Config
from utils.security import log_error

_STOPWORDS = {
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if",
    "in", "is", "it", "its", "me", "my", "of", "on", "or", "should", "so", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "we",
    "were", "what", "when", "which", "who", "why", "will", "with", "would", "you", "your",
}

_REFUSAL_PATTERNS = [
    (r"\b(?:do i have|am i|is this|could (?:i|it) be|diagnos)\w*\b.*\b(?:cancer|covid|diabetes|infection|disease|disorder|condition)\b",
     "diagnosis"),
    (r"\bwhat (?:is )?(?:wrong|the matter) with me\b", "diagnosis"),
    (r"\b(?:diagnose|diagnosis) (?:me|my)\b", "diagnosis"),
    (r"\b(?:should|can|may) i (?:stop|start|double|halve|skip|increase|decrease|take more|take less)\b",
     "treatment-change"),
    (r"\b(?:what|which) (?:medicine|medication|drug|antibiotic|dose) should i (?:take|use|start)\b",
     "prescribing"),
    (r"\b(?:prescribe|recommend) (?:me|a|an|some)\b", "prescribing"),
    (r"\b(?:change|adjust|increase|reduce|lower|raise) (?:my )?dose\b", "treatment-change"),
    (r"\bis it (?:safe|ok|okay|fine) to (?:stop|skip|double|combine)\b", "treatment-change"),
]

_REFUSAL_TEXT = {
    "diagnosis": (
        "I can't diagnose conditions or tell you what is causing a symptom - that needs a "
        "clinician who can examine you and see your test results. What I can do is explain "
        "what your prescription says, what each medicine is generally used for, and what "
        "safety findings were flagged. If a symptom is new, severe or worrying, contact your "
        "doctor, and use emergency services for the red-flag symptoms listed in your safety report."
    ),
    "treatment-change": (
        "I can't advise you to start, stop, skip, combine or change the dose of a medicine - "
        "that decision belongs to your prescriber or pharmacist, who know your full history. "
        "I can explain what the prescription instructs, what interaction was flagged and why, "
        "and what to ask when you call them."
    ),
    "prescribing": (
        "I can't recommend or choose a medicine for you; only a prescriber can do that. I can "
        "explain the medicines already in your prescription, what they are generally used for, "
        "and the safety findings for the combination you entered."
    ),
}


# --------------------------------------------------------------------------
# Corpus construction
# --------------------------------------------------------------------------
_REFERENCE_DOCS = None


def reference_docs():
    global _REFERENCE_DOCS
    if _REFERENCE_DOCS is None:
        try:
            raw = json.loads((DATA_DIR / "medical_knowledge.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover
            log_error("loading knowledge corpus", exc)
            raw = []
        _REFERENCE_DOCS = [
            {
                "id": d["id"],
                "title": d["title"],
                "text": d["text"],
                "tags": d.get("tags", []),
                "source_name": d.get("source_name"),
                "source_url": d.get("source_url"),
                "kind": "reference",
            }
            for d in raw
        ]
    return _REFERENCE_DOCS


def build_corpus(context=None) -> list[dict]:
    """Assemble reference documents plus patient-specific documents."""
    docs = list(reference_docs())
    context = context or {}

    for item in context.get("items", []) or []:
        name = item.get("display_name") or item.get("medication")
        if not name:
            continue
        parts = ["%s is in this prescription." % name]
        if item.get("drug_class"):
            parts.append("It is classified as: %s." % item["drug_class"])
        if item.get("common_uses"):
            parts.append("Common uses recorded in the dataset: %s" % item["common_uses"])
        sig = []
        for field, label in (("strength", "strength"), ("dose", "dose"),
                             ("frequency", "frequency"), ("duration", "duration"),
                             ("route", "route"), ("instructions", "special instructions")):
            if item.get(field):
                sig.append("%s: %s" % (label, item[field]))
        if sig:
            parts.append("As written in this prescription - " + "; ".join(sig) + ".")
        if item.get("needs_verification"):
            parts.append(
                "Not stated in the prescription text and needing verification: %s."
                % ", ".join(item["needs_verification"])
            )
        if item.get("rxcui"):
            parts.append("RxNorm identifier (RXCUI): %s." % item["rxcui"])
        docs.append({
            "id": "med:%s" % (item.get("normalized_name") or name.lower()),
            "title": "%s - prescription details and medication record" % name,
            "text": " ".join(parts),
            "tags": [name.lower(), "medication", "dose", "frequency", "duration"],
            "source_name": item.get("normalization_source") or "Local medication dataset",
            "source_url": item.get("normalization_source_url"),
            "kind": "prescription",
        })
        if item.get("fda_label", {}).get("sections"):
            for section, text in item["fda_label"]["sections"].items():
                docs.append({
                    "id": "fda:%s:%s" % (name.lower(), section),
                    "title": "%s - openFDA label: %s" % (name, section.replace("_", " ")),
                    "text": text,
                    "tags": [name.lower(), section],
                    "source_name": "openFDA drug label API",
                    "source_url": "https://open.fda.gov/apis/drug/label/",
                    "kind": "label",
                })

    report = context.get("report") or {}
    for i, finding in enumerate(report.get("interactions", []) or []):
        docs.append({
            "id": "finding:interaction:%d" % i,
            "title": "Flagged interaction: %s + %s (%s concern)"
                     % (finding.get("drug_a"), finding.get("drug_b"), finding.get("severity")),
            "text": " ".join(filter(None, [
                finding.get("description"),
                "Mechanism: %s" % finding.get("mechanism") if finding.get("mechanism") else None,
                "What is usually done: %s" % finding.get("management") if finding.get("management") else None,
                "Severity category: %s." % finding.get("severity"),
            ])),
            "tags": ["interaction", str(finding.get("drug_a", "")).lower(),
                     str(finding.get("drug_b", "")).lower(), finding.get("severity", "")],
            "source_name": finding.get("source_name"),
            "source_url": finding.get("source_url"),
            "kind": "finding",
        })
    for i, finding in enumerate(report.get("allergy_warnings", []) or []):
        docs.append({
            "id": "finding:allergy:%d" % i,
            "title": "Allergy warning: %s vs recorded allergy '%s'"
                     % (finding.get("medication"), finding.get("allergy")),
            "text": " ".join(filter(None, [finding.get("description"), finding.get("management"),
                                           finding.get("mechanism")])),
            "tags": ["allergy", str(finding.get("medication", "")).lower()],
            "source_name": finding.get("source_name"),
            "source_url": finding.get("source_url"),
            "kind": "finding",
        })
    for i, finding in enumerate(report.get("condition_warnings", []) or []):
        docs.append({
            "id": "finding:condition:%d" % i,
            "title": "Condition caution: %s with '%s'"
                     % (finding.get("medication"), finding.get("condition")),
            "text": " ".join(filter(None, [finding.get("description"), finding.get("management")])),
            "tags": ["condition", str(finding.get("condition", "")).lower(),
                     str(finding.get("medication", "")).lower()],
            "source_name": finding.get("source_name"),
            "source_url": finding.get("source_url"),
            "kind": "finding",
        })

    for i, finding in enumerate(report.get("dose_warnings", []) or []):
        docs.append({
            "id": "finding:dose:%d" % i,
            "title": "Dosage check: %s" % finding.get("medication"),
            "text": " ".join(filter(None, [
                finding.get("description"),
                "Working: %s." % finding.get("evidence") if finding.get("evidence") else None,
                finding.get("management"),
            ])),
            "tags": ["dose", "dosage", "maximum", "daily", str(finding.get("medication", "")).lower()],
            "source_name": finding.get("source_name"),
            "source_url": finding.get("source_url"),
            "kind": "finding",
        })
    for i, rule in enumerate(report.get("timing_guidance", []) or []):
        docs.append({
            "id": "finding:timing:%d" % i,
            "title": "Food and timing: %s" % rule.get("medication"),
            "text": " ".join(filter(None, [
                rule.get("guidance"),
                "Why: %s" % rule.get("reason") if rule.get("reason") else None,
                "Separate by about %s hours from %s." % (rule["spacing_hours"], rule.get("spacing_with"))
                if rule.get("spacing_hours") else None,
            ])),
            "tags": ["food", "timing", "meal", "empty stomach", "alcohol", "grapefruit", "spacing",
                     str(rule.get("medication", "")).lower()],
            "source_name": rule.get("source_name"),
            "source_url": rule.get("source_url"),
            "kind": "finding",
        })

    for line in context.get("graph_facts", []) or []:
        docs.append({
            "id": "graph:%d" % len(docs),
            "title": "Knowledge-graph relationship",
            "text": line,
            "tags": ["graph", "relationship"],
            "source_name": "MediGraph AI knowledge graph (built from your own records)",
            "source_url": None,
            "kind": "graph",
        })

    history = context.get("history_summary")
    if history:
        docs.append({
            "id": "history:summary",
            "title": "Medication history for this patient profile",
            "text": history,
            "tags": ["history", "previous", "past", "completed"],
            "source_name": "MediGraph AI database (your own saved records)",
            "source_url": None,
            "kind": "history",
        })
    return docs


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------
def _stem(token: str) -> str:
    """Very light suffix stripping so 'interactions' matches 'interaction'."""
    for suffix in ("ies", "ses", "es", "s", "ing", "ed"):
        if len(token) > 4 and token.endswith(suffix):
            base = token[: -len(suffix)]
            return base + "y" if suffix == "ies" else base
    return token


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", str(text).lower())
    return [_stem(t) for t in tokens if t not in _STOPWORDS and len(t) > 1]


class TfidfRetriever:
    engine = "TF-IDF cosine similarity (pure Python)"

    def __init__(self, docs):
        self.docs = docs
        self.doc_tokens = [tokenize("%s %s %s" % (d["title"], " ".join(d.get("tags", [])), d["text"]))
                           for d in docs]
        self.df = Counter()
        for tokens in self.doc_tokens:
            self.df.update(set(tokens))
        self.n = max(len(docs), 1)
        self.vectors = [self._vector(tokens) for tokens in self.doc_tokens]

    def _idf(self, term: str) -> float:
        return math.log((1 + self.n) / (1 + self.df.get(term, 0))) + 1.0

    def _vector(self, tokens):
        counts = Counter(tokens)
        vec = {t: (1 + math.log(c)) * self._idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def search(self, question: str, k: int = 4, min_score: float = 0.055):
        qvec = self._vector(tokenize(question))
        scored = []
        for doc, dvec in zip(self.docs, self.vectors):
            score = sum(w * dvec.get(t, 0.0) for t, w in qvec.items())
            # Small boost for patient-specific documents
            if doc.get("kind") in ("finding", "prescription"):
                score *= 1.18
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [(round(s, 4), d) for s, d in scored[:k] if s >= min_score]


class EmbeddingRetriever:  # pragma: no cover - optional heavy dependency
    """FAISS + sentence-transformers retriever, used only when installed."""

    engine = "FAISS dense embeddings (sentence-transformers)"

    def __init__(self, docs):
        import faiss  # type: ignore
        import numpy as np  # type: ignore
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.np = np
        self.docs = docs
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        texts = ["%s. %s" % (d["title"], d["text"]) for d in docs]
        matrix = self.model.encode(texts, normalize_embeddings=True)
        self.index = faiss.IndexFlatIP(matrix.shape[1])
        self.index.add(matrix.astype("float32"))

    def search(self, question: str, k: int = 4, min_score: float = 0.25):
        qv = self.model.encode([question], normalize_embeddings=True).astype("float32")
        scores, idx = self.index.search(qv, min(k, len(self.docs)))
        out = []
        for score, i in zip(scores[0], idx[0]):
            if i >= 0 and score >= min_score:
                out.append((round(float(score), 4), self.docs[i]))
        return out


def make_retriever(docs):
    try:
        return EmbeddingRetriever(docs)
    except Exception:
        return TfidfRetriever(docs)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are MediGraph AI's prescription explainer. Follow these rules exactly:\n"
    "1. Answer ONLY from the CONTEXT passages provided. Never add medical facts from memory.\n"
    "2. If the context does not answer the question, say so plainly and suggest asking a "
    "pharmacist. Do not speculate.\n"
    "3. Never diagnose a condition, never recommend starting, stopping, combining or changing "
    "a medicine or dose, and never state that a combination is safe.\n"
    "4. Quote dosage details only as they appear in the prescription context.\n"
    "5. Write in plain language, short paragraphs or bullets, for a patient or caregiver.\n"
    "6. End with one line reminding the reader to confirm with their doctor or pharmacist.\n"
    "7. Cite the passages you used by their [n] markers."
)


def call_llm(prompt: str, system: str = "", max_tokens: int = 900):
    """Call the configured LLM provider. Returns text, or None when unavailable."""
    if not Config.llm_enabled():
        return None
    try:
        if Config.LLM_PROVIDER == "anthropic":
            body = json.dumps({
                "model": Config.ANTHROPIC_MODEL,
                "max_tokens": max_tokens,
                "system": system or _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=body,
                headers={
                    "content-type": "application/json",
                    "x-api-key": Config.ANTHROPIC_API_KEY or "",
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            blocks = data.get("content") or []
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

        if Config.LLM_PROVIDER == "openai":
            body = json.dumps({
                "model": Config.OPENAI_MODEL,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system or _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=body,
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer %s" % (Config.OPENAI_API_KEY or ""),
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return (data["choices"][0]["message"]["content"] or "").strip()
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        log_error("LLM call", exc)
        return None
    return None


def _split_sentences(text: str):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text)) if s.strip()]


def _extractive_answer(question: str, hits) -> str:
    """Compose an answer from the retrieved passages without an LLM."""
    q_tokens = set(tokenize(question))
    chosen = []
    for rank, (_score, doc) in enumerate(hits, start=1):
        # Patient-specific passages are short and every detail matters, so quote them whole.
        if doc.get("kind") in ("prescription", "finding", "graph", "history"):
            chosen.append("**%s** [%d]\n\n%s" % (doc["title"], rank, doc["text"][:900]))
            continue
        sentences = _split_sentences(doc["text"])
        scored = []
        for sentence in sentences:
            overlap = len(q_tokens & set(tokenize(sentence)))
            scored.append((overlap, sentences.index(sentence), sentence))
        scored.sort(key=lambda t: (-t[0], t[1]))
        best = [s for overlap, _i, s in scored[:2] if overlap > 0] or sentences[:1]
        if best:
            chosen.append("**%s** [%d]\n\n%s" % (doc["title"], rank, " ".join(best)))
    if not chosen:
        return ""
    body = "\n\n".join(chosen)
    return (
        "Here is what the sources on record say:\n\n%s\n\n"
        "_This is assembled directly from the passages above, with no AI-generated medical "
        "content added. Confirm anything that affects what you take with your doctor or "
        "pharmacist._" % body
    )


def check_guardrails(question: str):
    lowered = " %s " % str(question or "").lower()
    for pattern, kind in _REFUSAL_PATTERNS:
        if re.search(pattern, lowered):
            return kind
    return None


def answer_question(question: str, context=None, *, k: int = 4):
    """Answer a question with retrieval-augmented generation.

    Returns {"answer", "sources", "retrieved", "mode", "refused", "engine"}.
    """
    question = (question or "").strip()
    result = {
        "answer": "",
        "sources": [],
        "retrieved": [],
        "mode": "none",
        "refused": None,
        "engine": "n/a",
    }
    if not question:
        result["answer"] = "Ask a question about your prescription, a medicine in it, or a finding in your safety report."
        return result

    refusal = check_guardrails(question)
    if refusal:
        result["refused"] = refusal
        result["mode"] = "guardrail"
        result["answer"] = _REFUSAL_TEXT[refusal]
        return result

    docs = build_corpus(context)
    retriever = make_retriever(docs)
    result["engine"] = retriever.engine
    hits = retriever.search(question, k=k)
    result["retrieved"] = [
        {"title": d["title"], "score": s, "kind": d.get("kind"),
         "source_name": d.get("source_name"), "source_url": d.get("source_url")}
        for s, d in hits
    ]
    result["sources"] = [
        {"title": d["title"], "source_name": d.get("source_name"), "source_url": d.get("source_url")}
        for _s, d in hits
    ]

    if not hits:
        result["mode"] = "no-context"
        result["answer"] = (
            "I don't have reliable information on that in the sources available to me "
            "(your prescription records, the curated interaction dataset and the reference "
            "library). I would rather say so than guess. Your pharmacist can answer it "
            "directly, and the patient information leaflet in the medicine packet is a good "
            "second source."
        )
        return result

    passages = "\n\n".join(
        "[%d] %s\nSource: %s\n%s" % (i, d["title"], d.get("source_name") or "n/a", d["text"])
        for i, (_s, d) in enumerate(hits, start=1)
    )
    llm_answer = call_llm(
        "CONTEXT:\n%s\n\nQUESTION: %s\n\nAnswer using only the context above." % (passages, question),
        system=_SYSTEM_PROMPT,
    )
    if llm_answer:
        result["mode"] = "llm+rag (%s)" % Config.LLM_PROVIDER
        result["answer"] = llm_answer
    else:
        result["mode"] = "extractive rag (no LLM key configured)"
        result["answer"] = _extractive_answer(question, hits) or (
            "I found related material but nothing that answers the question directly. "
            "Please ask your pharmacist."
        )
    return result


SUGGESTED_QUESTIONS = [
    "What is this medication generally used for?",
    "What does three times daily mean?",
    "What potential interactions were detected?",
    "Why was this interaction flagged?",
    "What information should I confirm with my pharmacist?",
    "What should I do if I miss a dose?",
    "Does my daily dose stay within the labelled maximum?",
    "Can I drink alcohol or grapefruit juice with these?",
    "Which symptoms mean I should seek emergency care?",
    "Should I take this with food?",
]
