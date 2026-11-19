"""OCR prescription intake: engine discovery, preprocessing, cleaning, safety rules."""
from __future__ import annotations

import io

import pytest

from backend import ocr, parser, pipeline
from database import models

PIL = pytest.importorskip("PIL", reason="Pillow is required for the image tests")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

TESSERACT_READY = any(
    e["id"] == "tesseract" and e["available"] for e in ocr.available_engines()
)


def _prescription_image(text: str, size=(900, 320), font_size: int = 34) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:  # pragma: no cover - platform dependent
        font = ImageFont.load_default()
    draw.multiline_text((30, 30), text, fill="black", font=font, spacing=18)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Engine discovery
# --------------------------------------------------------------------------
def test_every_engine_is_described_with_availability_and_a_reason():
    engines = {e["id"]: e for e in ocr.available_engines()}
    assert set(engines) == {"vision-llm", "tesseract", "easyocr", "pdf-text"}
    for engine in engines.values():
        assert isinstance(engine["available"], bool)
        assert engine["name"] and engine["handwriting"]
        if not engine["available"]:
            assert engine["reason"], engine["id"]


def test_engine_status_picks_a_default():
    status = ocr.engine_status()
    assert status["any_available"] is any(e["available"] for e in status["engines"])
    if status["any_available"]:
        assert status["default"] in (None, "tesseract", "easyocr", "vision-llm")


def test_describe_engines_is_a_single_line():
    assert "Tesseract" in ocr.describe_engines()


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
def test_empty_upload_is_rejected():
    result = ocr.extract_text(b"", "scan.png")
    assert not result["ok"] and "empty" in result["errors"][0]


def test_unsupported_file_type_is_rejected():
    result = ocr.extract_text(b"abc", "notes.docx")
    assert not result["ok"] and "Unsupported file type" in result["errors"][0]


def test_oversized_upload_is_rejected():
    result = ocr.extract_text(b"x" * (ocr.MAX_IMAGE_BYTES + 1), "huge.png")
    assert not result["ok"] and "limit is" in result["errors"][0]


def test_vision_llm_requires_explicit_consent(monkeypatch):
    monkeypatch.setattr(ocr.Config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(ocr.Config, "ANTHROPIC_API_KEY", "test-key")
    result = ocr.extract_text(_prescription_image("Amoxicillin 500mg"), "scan.png",
                              engine="vision-llm", allow_cloud=False)
    assert not result["ok"]
    assert "explicit consent" in result["errors"][0]
    assert "leaves this machine" in result["errors"][0]


def test_unknown_engine_is_reported():
    result = ocr.extract_text(_prescription_image("Amoxicillin"), "scan.png", engine="magic")
    assert not result["ok"] and "Unknown OCR engine" in result["errors"][0]


# --------------------------------------------------------------------------
# Preprocessing
# --------------------------------------------------------------------------
def test_preprocessing_returns_an_image_and_notes():
    processed, notes = ocr.preprocess(_prescription_image("Amoxicillin 500mg", size=(400, 120)))
    assert processed and processed[:4] == b"\x89PNG"
    assert any("greyscale" in n for n in notes)
    assert any("upscaled" in n for n in notes)


def test_preprocessing_never_raises_on_rubbish_input():
    processed, notes = ocr.preprocess(b"not an image at all")
    assert processed == b"not an image at all"
    assert notes and "preprocessing failed" in notes[0]


# --------------------------------------------------------------------------
# Text cleaning (must never alter clinical content)
# --------------------------------------------------------------------------
def test_cleaning_fixes_mangled_units_only():
    assert ocr.clean_ocr_text("Amoxicillin 500rng") == "Amoxicillin 500mg"
    assert ocr.clean_ocr_text("Levothyroxine 50 mcq") == "Levothyroxine 50mcg"
    assert ocr.clean_ocr_text("Metformin 500 m g") == "Metformin 500mg"


def test_cleaning_never_changes_numbers():
    text = "Warfarin 5mg once daily\nIbuprofen 400mg three times daily for 7 days"
    assert ocr.clean_ocr_text(text) == text


def test_cleaning_drops_noise_lines_and_trims():
    cleaned = ocr.clean_ocr_text("   \n~~~~~\nAmoxicillin 500mg\n***\n\n  Ibuprofen 400mg  ")
    assert cleaned.splitlines() == ["Amoxicillin 500mg", "Ibuprofen 400mg"]


def test_cleaning_handles_empty_input():
    assert ocr.clean_ocr_text("") == ""
    assert ocr.clean_ocr_text(None) == ""


# --------------------------------------------------------------------------
# Real OCR (skipped when Tesseract is not installed)
# --------------------------------------------------------------------------
@pytest.mark.skipif(not TESSERACT_READY, reason="Tesseract is not installed")
def test_tesseract_reads_a_printed_prescription():
    image = _prescription_image(
        "Amoxicillin 500mg\nTake one capsule three times daily for 5 days\n"
        "Ibuprofen 400mg BD after food x 7 days"
    )
    result = ocr.extract_text(image, "scan.png")
    assert result["ok"]
    assert result["engine"] == "tesseract"
    assert "Amoxicillin" in result["text"]
    assert "500mg" in result["text"].replace(" ", "")
    assert result["confidence"] > 50
    assert result["lines_detected"] == 3


@pytest.mark.skipif(not TESSERACT_READY, reason="Tesseract is not installed")
def test_every_scan_carries_the_verification_warning():
    result = ocr.extract_text(_prescription_image("Amoxicillin 500mg once daily"), "scan.png")
    assert any("machine transcription, not a prescription" in w for w in result["warnings"])
    assert any("handwriting poorly" in w for w in result["warnings"])


@pytest.mark.skipif(not TESSERACT_READY, reason="Tesseract is not installed")
def test_blank_image_reports_failure_rather_than_empty_success():
    blank = _prescription_image("", size=(600, 200))
    result = ocr.extract_text(blank, "blank.png")
    assert not result["ok"]
    assert "No text could be read" in result["errors"][0]


@pytest.mark.skipif(not TESSERACT_READY, reason="Tesseract is not installed")
def test_scanned_text_flows_into_the_parser():
    image = _prescription_image(
        "Amoxicillin 500mg\nTake one capsule three times daily for 5 days"
    )
    result = ocr.extract_text(image, "scan.png")
    parsed = parser.parse_prescription(result["text"])
    assert len(parsed["items"]) == 1, [i["medication"] for i in parsed["items"]]
    item = parsed["items"][0]
    assert item["normalized_name"] == "amoxicillin"
    assert item["frequency_per_day"] == 3
    assert item["duration_days"] == 5


# --------------------------------------------------------------------------
# Wrapped lines (how printed and scanned prescriptions actually look)
# --------------------------------------------------------------------------
def test_instruction_only_lines_join_the_medication_above():
    lines = parser.split_medication_lines(
        "Amoxicillin 500mg\nTake one capsule three times daily for 5 days\n"
        "Ibuprofen 400mg BD after food x 7 days"
    )
    assert len(lines) == 2
    assert lines[0].startswith("Amoxicillin") and "three times daily" in lines[0]
    assert lines[1].startswith("Ibuprofen")


def test_wrapped_prescription_parses_completely():
    result = parser.parse_prescription(
        "Tab Metformin 500mg\n1 tablet twice daily with meals\nx 30 days"
    )
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["normalized_name"] == "metformin"
    assert item["frequency_per_day"] == 2
    assert item["duration_days"] == 30


def test_unknown_medication_is_not_swallowed_as_a_continuation():
    lines = parser.split_medication_lines(
        "Amoxicillin 500mg three times daily\nZyxomycin 250mg twice daily for 5 days"
    )
    assert len(lines) == 2


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------
def test_scanned_prescriptions_are_stored_with_their_source(patient):
    outcome = pipeline.analyze(
        "Amoxicillin 500mg, take one capsule three times daily for 5 days",
        patient, save=True, source="ocr", ocr_engine="tesseract", ocr_confidence=88.5,
        ocr_verified=True,
    )
    row = models.get_prescription(outcome["prescription_id"])
    assert row["source"] == "ocr"
    assert row["ocr_engine"] == "tesseract"
    assert row["ocr_confidence"] == 88.5
    assert row["ocr_verified"] == 1


def test_typed_prescriptions_default_to_typed(patient):
    outcome = pipeline.analyze("Amoxicillin 500mg three times daily for 5 days",
                               patient, save=True)
    row = models.get_prescription(outcome["prescription_id"])
    assert row["source"] == "typed"
    assert row["ocr_engine"] is None


def test_migration_adds_columns_to_an_older_database(tmp_path):
    """A database created before OCR support gains the columns on startup."""
    import sqlite3

    from database.db import _migrate

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE prescriptions (id INTEGER PRIMARY KEY, patient_id INTEGER, "
        "raw_text TEXT, parsed_json TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO prescriptions (raw_text, parsed_json, created_at) VALUES ('x','[]','now')")
    conn.commit()
    conn.close()

    applied = _migrate(str(path))
    assert "prescriptions.source" in applied

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(prescriptions)")}
    row = conn.execute("SELECT source FROM prescriptions").fetchone()
    conn.close()
    assert {"source", "ocr_engine", "ocr_confidence", "ocr_verified"} <= columns
    assert row[0] == "typed"  # existing rows default to typed
    assert _migrate(str(path)) == []  # idempotent


# --------------------------------------------------------------------------
# Letterhead filtering (scans pick up far more than the medication list)
# --------------------------------------------------------------------------
def test_letterhead_lines_are_skipped_with_a_warning():
    result = parser.parse_prescription(
        "Dr. A. Sharma - City Clinic\nRx\nAmoxicillin 500mg three times daily for 5 days\n"
        "Signature"
    )
    assert [i["normalized_name"] for i in result["items"]] == ["amoxicillin"]
    assert any("letterhead" in w for w in result["warnings"])


def test_a_medication_line_is_never_mistaken_for_letterhead():
    # Starts with a header-ish word but carries real dosing information.
    result = parser.parse_prescription("Dr. Reddys Amoxicillin 500mg three times daily for 5 days")
    assert result["items"] and result["items"][0]["normalized_name"] == "amoxicillin"
    assert result["items"][0]["strength"] == "500mg"


@pytest.mark.skipif(not TESSERACT_READY, reason="Tesseract is not installed")
def test_full_scan_of_a_realistic_prescription(tmp_path):
    image = _prescription_image(
        "Dr. A. Sharma - City Clinic\n\nRx\n"
        "1. Tab Amoxicillin 500mg\n"
        "   Take one capsule three times daily for 5 days\n"
        "2. Tab Ibuprofen 400mg\n"
        "   1 tablet twice daily after food x 7 days",
        size=(1000, 420), font_size=30,
    )
    result = ocr.extract_text(image, "scan.png")
    assert result["ok"]
    parsed = parser.parse_prescription(result["text"])
    names = [i["normalized_name"] for i in parsed["items"]]
    assert names == ["amoxicillin", "ibuprofen"], parsed["items"]
    amoxicillin = parsed["items"][0]
    assert amoxicillin["strength"] == "500mg"
    assert amoxicillin["frequency_per_day"] == 3
    assert amoxicillin["duration_days"] == 5
