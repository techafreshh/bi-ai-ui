"""SQLite vs Postgres source-contract shim at the issue's literal path.

The canonical contract test lives at `tests/test_postgres_contract.py` (the
repo's test home: `pyproject.toml` pins `testpaths=["tests"]`); this module
re-exports its fixtures and tests so the issue's acceptance command
`pytest agent/test_source_contract.py -q` (from the repo root) collects the
same suite. The `tests.`-qualified import keeps a distinct module name, so
pytest's basename import of this file never collides with the canonical
module (renamed with a distinct basename for the same reason under issue
#91); `REPO_ROOT`/`SAMPLES_DIR` still resolve from the canonical file.
"""

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from tests.test_postgres_contract import (  # noqa: E402, F401
    outer_pg_url,
    sqlite_samples,
    test_datasets_lists_postgres_source_or_skip,
    test_postgres_parity_or_skip,
    test_postgres_read_only_or_skip,
    test_postgres_ro_guard_or_skip,
    test_sqlite_latest_event_day_format_and_kpis,
)
