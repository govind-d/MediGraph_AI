"""Optional FastAPI REST layer over the same backend modules.

The Streamlit app does not need this: it calls `backend.pipeline` directly.
The API exists so the same engine can be driven by another client (mobile app,
React front-end, hospital integration) exactly as the architecture diagram
describes, and so the pipeline can be exercised from `curl` or Postman.

Run it with:

    python -m pip install "fastapi[standard]" uvicorn
    python -m uvicorn api.main:app --reload --port 8000

Interactive documentation is then at http://localhost:8000/docs

Note on scope: endpoints that touch patient records require the user id and
patient id as explicit parameters. This prototype has no token-based API
authentication - do not expose it beyond localhost.
"""
from __future__ import annotations

import base64
import binascii
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - optional dependency
    raise SystemExit(
        "FastAPI is not installed. Run:  python -m pip install \"fastapi[standard]\" uvicorn"
    ) from exc

from backend import (medication_normalizer, ocr, parser, pipeline, rag_engine,
                     safety_report, scheduler)
from backend.interaction_checker import run_full_check
from database import models
from database.db import ensure_db, init_db
from utils.config import DISCLAIMER, EMERGENCY_GUIDANCE, Config

@asynccontextmanager
async def lifespan(_app):
    """Create the database and seed the reference datasets before serving."""
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="MediGraph AI API",
    version="1.0.0",
    description=(
        "Prescription parsing, medication normalisation, knowledge-graph mapping, "
        "interaction/allergy/condition/dose checking and RAG question answering.\n\n"
        "**" + DISCLAIMER.replace("**", "") + "**"
    ),
)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class ParseRequest(BaseModel):
    text: str = Field(..., examples=["Amoxicillin 500mg, take one capsule three times daily for five days."])


class AnalyzeRequest(BaseModel):
    text: str
    patient_id: int | None = Field(None, description="Use a stored profile's allergies and conditions.")
    age: int | None = None
    weight_kg: float | None = None
    is_pregnant: bool = False
    allergies: list[str] = []
    conditions: list[str] = []
    save: bool = False


class ChatRequest(BaseModel):
    question: str
    patient_id: int | None = None


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/health", tags=["system"])
def health():
    ensure_db()
    return {
        "status": "ok",
        "sources": Config.describe_sources(),
        "llm_enabled": Config.llm_enabled(),
        "disclaimer": DISCLAIMER.replace("**", ""),
    }


@app.post("/parse", tags=["nlp"])
def parse(request: ParseRequest):
    """Structure prescription text. Missing fields are reported, never invented."""
    result = parser.parse_prescription(request.text)
    if result["errors"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    return {
        "items": parser.to_simple_json(result["items"]),
        "detail": result["items"],
        "warnings": result["warnings"],
        "parser": result["parser"],
    }


@app.get("/normalize/{name}", tags=["nlp"])
def normalize(name: str):
    payload = medication_normalizer.normalize(name)
    if not payload:
        raise HTTPException(status_code=404,
                            detail="'%s' was not found in the checked sources." % name)
    return payload


@app.post("/analyze", tags=["safety"])
def analyze(request: AnalyzeRequest):
    """Full pipeline: parse, normalise, graph, interaction/allergy/condition/dose checks."""
    if request.patient_id:
        patient = models.get_profile(request.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Unknown patient_id")
        outcome = pipeline.analyze(request.text, patient, save=request.save)
        if outcome["errors"]:
            raise HTTPException(status_code=422, detail=outcome["errors"])
        return {
            "report": outcome["report"],
            "graph": outcome["graph"]["payload"] if outcome.get("graph") else None,
            "prescription_id": outcome["prescription_id"],
            "warnings": outcome["warnings"],
        }

    parsed = parser.parse_prescription(request.text)
    if parsed["errors"]:
        raise HTTPException(status_code=422, detail=parsed["errors"])
    items = medication_normalizer.normalize_items(parsed["items"])
    findings = run_full_check(
        items,
        allergies=[{"substance": a} for a in request.allergies],
        conditions=[{"condition_name": c} for c in request.conditions],
        is_pregnant=request.is_pregnant,
        age=request.age,
        weight_kg=request.weight_kg,
    )
    report = safety_report.build_report(
        patient={"name": "ad-hoc request", "age": request.age,
                 "is_pregnant": request.is_pregnant},
        items=items, findings=findings, raw_text=request.text,
        allergies=[{"substance": a} for a in request.allergies],
        conditions=[{"condition_name": c} for c in request.conditions],
        parser_info=parsed["parser"],
    )
    return {"report": report, "graph": None, "prescription_id": None,
            "warnings": parsed["warnings"]}


class OcrRequest(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded image or PDF bytes.")
    filename: str = Field("upload.png", description="Used only to detect the file type.")
    engine: str = Field("auto", description="auto | tesseract | easyocr | vision-llm | pdf-text")
    preprocess: bool = True
    allow_cloud: bool = Field(
        False, description="Must be true before an image may be sent to a vision LLM."
    )


@app.get("/ocr/engines", tags=["ocr"])
def ocr_engines():
    """Which OCR engines this installation can use, and how to enable the rest."""
    return ocr.engine_status()


@app.post("/ocr", tags=["ocr"])
def ocr_transcribe(request: OcrRequest):
    """Transcribe a prescription image.

    The response is a **draft transcription for a human to check**, never a
    prescription. Feed the corrected text to `/analyze` yourself; this endpoint
    deliberately does not analyse what it reads.
    """
    try:
        payload = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="image_base64 is not valid base64.")
    result = ocr.extract_text(
        payload, request.filename, engine=request.engine,
        preprocess_image=request.preprocess, allow_cloud=request.allow_cloud,
    )
    if not result["ok"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    result["next_step"] = (
        "Show this text to the person who holds the prescription, have them correct it, then "
        "POST the corrected text to /analyze."
    )
    return result


@app.post("/chat", tags=["rag"])
def chat(request: ChatRequest):
    """Answer a question from retrieved, cited passages. Refuses diagnosis and prescribing."""
    context = pipeline.rag_context(request.patient_id) if request.patient_id else {}
    result = rag_engine.answer_question(request.question, context)
    return result


@app.get("/patients/{patient_id}/schedule", tags=["adherence"])
def schedule(patient_id: int, days: int = 3):
    if not models.get_profile(patient_id):
        raise HTTPException(status_code=404, detail="Unknown patient_id")
    return {
        "today": scheduler.day_plan(patient_id),
        "upcoming": scheduler.upcoming_doses(patient_id, days=days),
        "adherence": scheduler.adherence_summary(patient_id, days=7),
        "timing_guidance": scheduler.timing_guidance_for_patient(patient_id),
    }


@app.get("/patients/{patient_id}/history", tags=["records"])
def history(patient_id: int):
    if not models.get_profile(patient_id):
        raise HTTPException(status_code=404, detail="Unknown patient_id")
    return {
        "prescriptions": models.list_prescriptions(patient_id),
        "current_medications": models.current_medications(patient_id),
        "reports": models.list_safety_reports(patient_id, limit=20),
        "summary": pipeline.history_summary(patient_id),
    }


@app.get("/emergency", tags=["system"])
def emergency():
    return {"guidance": EMERGENCY_GUIDANCE}
