"""Shared fixtures: a hermetic, deterministically seeded store.

``BI_DATA_DIR`` is pinned to a throwaway directory for the whole pytest run
and the seed is built once, so no test ever reads or writes the developer's
``data/app.db`` and every test observes exactly the planted shapes (PRD §7).
The seed is read-only for everything except ``test_store.py``, which builds
its own fresh databases.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="bi-ai-ui-test-data-")
os.environ["BI_DATA_DIR"] = _TEST_DATA_DIR
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)

import store  # noqa: E402 — env must be pinned before the store resolves its path

store.ensure_seeded(reset=True)
