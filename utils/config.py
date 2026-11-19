"""Central configuration for MediGraph AI.

All secrets come from environment variables (optionally loaded from a local
`.env` file). Nothing sensitive is ever hard-coded here.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"
EXPORT_DIR.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    """Minimal .env loader (no external dependency required)."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Database -------------------------------------------------------
    DB_PATH: str = os.getenv("MEDIGRAPH_DB", str(BASE_DIR / "medigraph.db"))
    SCHEMA_PATH: Path = BASE_DIR / "database" / "schema.sql"

    # --- Security -------------------------------------------------------
    PBKDF2_ITERATIONS: int = int(os.getenv("PBKDF2_ITERATIONS", "240000"))
    SESSION_TIMEOUT_MIN: int = int(os.getenv("SESSION_TIMEOUT_MIN", "120"))

    # --- Neo4j (optional; NetworkX fallback used when unavailable) ------
    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
    USE_NEO4J: bool = _flag("USE_NEO4J", "false")

    # --- External drug APIs (optional; local dataset fallback) ----------
    USE_RXNORM_API: bool = _flag("USE_RXNORM_API", "false")
    RXNORM_BASE_URL: str = os.getenv("RXNORM_BASE_URL", "https://rxnav.nlm.nih.gov/REST")
    USE_OPENFDA_API: bool = _flag("USE_OPENFDA_API", "false")
    OPENFDA_BASE_URL: str = os.getenv("OPENFDA_BASE_URL", "https://api.fda.gov/drug/label.json")
    OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY")
    HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "8"))

    # --- LLM (optional; extractive answers used when absent) ------------
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "none").strip().lower()
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # --- NLP ------------------------------------------------------------
    SPACY_MODEL: str = os.getenv("SPACY_MODEL", "en_core_web_sm")
    USE_SPACY: bool = _flag("USE_SPACY", "true")

    @classmethod
    def llm_enabled(cls) -> bool:
        if cls.LLM_PROVIDER == "anthropic":
            return bool(cls.ANTHROPIC_API_KEY)
        if cls.LLM_PROVIDER == "openai":
            return bool(cls.OPENAI_API_KEY)
        return False

    @classmethod
    def describe_sources(cls) -> dict:
        """Human-readable status of each data source (shown in Settings)."""
        return {
            "Knowledge graph": "Neo4j" if cls.USE_NEO4J else "NetworkX (local, in-process)",
            "Medication normalization": (
                "RxNorm live API" if cls.USE_RXNORM_API else "Local RxNorm-derived dataset"
            ),
            "Drug labels / warnings": (
                "openFDA live API" if cls.USE_OPENFDA_API else "Local curated label extracts"
            ),
            "Interaction data": "Local curated dataset (DDInter / FDA-label derived)",
            "Chatbot answers": (
                "LLM (%s)" % cls.LLM_PROVIDER
                if cls.llm_enabled()
                else "Extractive retrieval (no LLM key configured)"
            ),
        }


DISCLAIMER = (
    "MediGraph AI is an educational decision-support prototype. It supports but "
    "does **not** replace a doctor or pharmacist. Never start, stop, change or "
    "combine medicines based on this tool. Always confirm with a licensed clinician."
)

EMERGENCY_GUIDANCE = (
    "Seek emergency care immediately (call your local emergency number) if you notice: "
    "difficulty breathing; swelling of the face, lips, tongue or throat; a spreading rash "
    "or blistering skin; chest pain; fainting or confusion; uncontrolled bleeding; or a "
    "seizure after taking a medicine."
)
