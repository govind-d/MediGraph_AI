"""Optional FastAPI REST layer (skipped when FastAPI is not installed)."""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI is an optional dependency")
pytest.importorskip("httpx", reason="httpx is needed for the FastAPI test client")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def test_health_reports_active_sources(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "Knowledge graph" in body["sources"]
    assert "does not replace" in body["disclaimer"]


def test_parse_endpoint_returns_the_documented_shape(client):
    response = client.post(
        "/parse",
        json={"text": "Amoxicillin 500mg, take one capsule three times daily for five days."},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["medication"] == "Amoxicillin"
    assert item["frequency"] == "3 times daily"
    assert item["needs_verification"] == []


def test_parse_endpoint_rejects_empty_text(client):
    assert client.post("/parse", json={"text": "   "}).status_code == 422


def test_analyze_endpoint_runs_the_whole_pipeline(client):
    response = client.post("/analyze", json={
        "text": "Warfarin 5mg once daily\nIbuprofen 400mg three times daily\n"
                "Acetaminophen 1000mg, take 2 tablets four times a day",
        "allergies": ["penicillin"],
        "conditions": ["asthma"],
        "age": 70,
    })
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["highest_severity"] == "high"
    assert report["interactions"]
    assert any(d["severity"] == "high" for d in report["dose_warnings"])
    assert report["timing_guidance"]
    assert report["limitations"]


def test_normalize_endpoint(client):
    assert client.get("/normalize/Brufen").json()["normalized_name"] == "ibuprofen"
    assert client.get("/normalize/zzzzqqq").status_code == 404


def test_chat_endpoint_answers_and_refuses(client):
    answer = client.post("/chat", json={"question": "What does three times daily mean?"}).json()
    assert answer["sources"]
    assert answer["refused"] is None
    refusal = client.post("/chat", json={"question": "Should I stop taking warfarin?"}).json()
    assert refusal["refused"] == "treatment-change"


def test_unknown_patient_returns_404(client):
    assert client.get("/patients/999999/schedule").status_code == 404
    assert client.get("/patients/999999/history").status_code == 404


def test_ocr_engine_listing(client):
    body = client.get("/ocr/engines").json()
    assert {e["id"] for e in body["engines"]} == {"vision-llm", "tesseract", "easyocr", "pdf-text"}


def test_ocr_endpoint_rejects_bad_base64(client):
    response = client.post("/ocr", json={"image_base64": "not base64!!", "filename": "x.png"})
    assert response.status_code == 422


def test_ocr_endpoint_transcribes_an_image(client):
    import base64
    import io

    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    from backend import ocr as ocr_module

    if not any(e["id"] == "tesseract" and e["available"] for e in ocr_module.available_engines()):
        pytest.skip("Tesseract is not installed")

    image = Image.new("RGB", (800, 160), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    draw.text((25, 40), "Amoxicillin 500mg three times daily", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    response = client.post("/ocr", json={
        "image_base64": base64.b64encode(buffer.getvalue()).decode(),
        "filename": "scan.png",
    })
    assert response.status_code == 200
    body = response.json()
    assert "Amoxicillin" in body["text"]
    assert body["warnings"]
    assert "correct it" in body["next_step"]
