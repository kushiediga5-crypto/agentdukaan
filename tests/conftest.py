"""Point the test DB at a temp path BEFORE any agentdukaan import."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="agentdukaan-tests-"))
os.environ["AGENTDUKAAN_DB_PATH"] = str(_TMP / "test.db")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    from agentdukaan import catalog, db

    db.init_db()
    catalog.ensure_seed()
    yield
