"""Test fixtures: every test runs against a throwaway SQLite database."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_medigraph.db"
    os.environ["MEDIGRAPH_DB"] = str(db_file)

    from utils.config import Config

    monkeypatch.setattr(Config, "DB_PATH", str(db_file))

    import database.db as db

    monkeypatch.setattr(db, "_INITIALISED", False, raising=False)
    db.init_db(str(db_file))
    yield db_file


@pytest.fixture
def user_id():
    from database import models

    uid, err = models.create_user("tester", "testpass123", "t@example.com", "Tester")
    assert not err
    return uid


@pytest.fixture
def patient(user_id):
    from database import models

    pid = models.create_profile(user_id, "Test Patient", "Self", 40, "female")
    return models.get_profile(pid)
