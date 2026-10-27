"""Shared pytest fixtures for the agent test suite (issue #20).

One seeded DB per pytest session: the generator's real entrypoint runs ONCE
here (~10s) and every consuming module reuses the same file, instead of each
module re-seeding its own copy (the old per-module session fixtures cost one
generator run each). The `db.DEFAULT_DB_PATH` swap that exposes the seed to
the query layer stays module-scoped — a session-long swap would leak past the
consuming module (golden_q3.py sorts before every test_*.py, and
test_generate_data's contract test asserts the pristine default), so the
swap is restored at module exit exactly like the per-module fixtures it
replaces (the idiom golden_q3.py documented before this consolidation).

The generator subprocess body mirrors the runtime gate (the same call shape
test_quality.py / test_queries.py carried before moving here — their single
home now). The sys.path bootstrap adds ONLY agent/ (not the repo root): the
agent modules dual-import db (package-style `from agent import db` first),
and repo-root visibility would flip every bind to agent.db while the tests
swap the top-level db module's DEFAULT_DB_PATH — two distinct module objects
would leave the swap seam silently dead. tests.test_graph's namespace-package
import resolves through agent/ alone.
"""

import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
GENERATOR = AGENT_DIR / "scripts" / "generate_data.py"
RUN_TIMEOUT_SECONDS = 600

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import db  # noqa: E402  (needs the sys.path bootstrap above)


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory):
    """Seed ONE temp DB for the whole pytest session through the generator's
    real entrypoint (subprocess, like the runtime gate) and return its path.
    This is the suite's only generator run outside test_generate_data.py's
    own determinism contract, which still seeds its own DB A / DB B / rerun
    through its `generated` fixture."""
    db_path = tmp_path_factory.mktemp("shared-seed") / "seeded.db"
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--db-path", str(db_path)],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return db_path


@pytest.fixture(scope="module")
def seeded_db(seeded_db_path):
    """While a consuming module's tests run, the query layer's module default
    points at the session seed so every public call reads the seeded data;
    the default is restored in finally so nothing leaks past the module
    (test_generate_data asserts the pristine default downstream). Yields the
    seeded path, mirroring the per-module fixtures this replaces."""
    original = db.DEFAULT_DB_PATH
    db.DEFAULT_DB_PATH = seeded_db_path
    try:
        yield seeded_db_path
    finally:
        db.DEFAULT_DB_PATH = original
