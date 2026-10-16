import os
import sys
from pathlib import Path

# Tests never call the live routing / weather / geocoding services.
os.environ["ROUTE_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src import config, db, fleet  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test gets its own SQLite file and never rewrites the committed route cache."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fleet, "save_route_cache", lambda: None)
    db.init_db()
    yield


@pytest.fixture
def scenario():
    """The demo fleet and disruption feed, built offline from the committed caches."""
    info = fleet.reset_scenario(allow_network=False)
    return info
