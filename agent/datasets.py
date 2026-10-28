"""Single-active sandbox dataset registry (issue #75).

One process-wide active dataset: `GET /data/datasets` lists every sandbox
(`data/sandboxes/<id>.db`) with its profile (`id`, `source`, `tables`,
`rows`, `data_end`) and `POST /data/active {id}` moves the single active
pointer every later read goes through, so queries can never leak rows
across datasets by construction — the world-switch is explicit, never
ambient.

The registry is throwaway (PRD v2): an in-memory `{id -> {source, path}}`
map plus a live filesystem scan of `data/sandboxes/*.db` on every list.
Orphans the scan finds but nobody registered — files `POST /data/upload`
wrote (the uploader never registers; activation is a separate explicit
switch), or files a probe dropped — report `source` `"upload"`. There is
no cross-restart persistence: a restart resets the active id to None.

`db.connect()` resolves its default path through `active_path()` and falls
back to `DEFAULT_DB_PATH` while no dataset is active, so every existing
caller (`queries.py`, `quality.py`, graph `read_state`) is rescoped with
no query-logic changes.

Stdlib only: `sqlite3`, `re`, `threading` and `pathlib`, so importing this
module never touches the agent's model dependencies.
"""

import re
import sqlite3
import threading
from pathlib import Path

# Resolved from this file's location, never from the cwd — the same
# anchoring as db.DEFAULT_DB_PATH/db.SANDBOX_DIR, so every entrypoint
# (script, test, server) scans the same repo-root `data/sandboxes`.
SANDBOX_DIR = Path(__file__).resolve().parents[1] / "data" / "sandboxes"

# Only letters, digits, `_` and `-`: `<id>.db` can never escape the sandbox
# directory, mirroring sandbox.py's `_DATASET_ID_RE`.
_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Origins a dataset may report; uploads and filesystem orphans are
# "upload", later issues register "sample" / "postgres" explicitly.
_ALLOWED_SOURCES = ("upload", "sample", "postgres")

# The only tables a profile counts (the generator's four), sorted in the
# response; anything else in the file is not event/account data.
_KNOWN_TABLES = (
    "accounts",
    "revenue_events",
    "subscription_events",
    "usage_events",
)

# Tables carrying `event_date`; `data_end` is their union-MAX (the same
# union-MAX shape queries.py uses for its latest-event anchor), None when
# none of them holds a date.
_EVENT_TABLES = (
    "subscription_events",
    "revenue_events",
    "usage_events",
)

_LOCK = threading.Lock()
# id -> {"source": str, "path": Path}; guarded by _LOCK.
_BY_ID: dict = {}
# The single process-wide active id; None means no active dataset.
_ACTIVE_ID = None


def _require_id_shape(dataset_id):
    """The sandbox id must be a non-empty `[A-Za-z0-9_-]+` string (no
    `../` escapes, no whitespace-only ids); anything else is ValueError
    before any I/O — the same fail-before-open idiom queries.py uses."""
    if not isinstance(dataset_id, str) or not _DATASET_ID_RE.match(dataset_id):
        raise ValueError(
            f"dataset_id must match [A-Za-z0-9_-]+, got {dataset_id!r}"
        )
    return dataset_id


def register(dataset_id, path, source="upload"):
    """Remember `dataset_id` at `path` with origin `source` (one of
    "upload" | "sample" | "postgres"). Registration alone never moves the
    active pointer — only `set_active` does. ValueError on a bad id or an
    unknown source. Returns the dataset id."""
    _require_id_shape(dataset_id)
    if source not in _ALLOWED_SOURCES:
        raise ValueError(
            f"source must be one of {list(_ALLOWED_SOURCES)}, got {source!r}"
        )
    with _LOCK:
        _BY_ID[dataset_id] = {"source": source, "path": Path(path)}
    return dataset_id


def set_active(dataset_id):
    """Move the process-wide active pointer to `dataset_id`.

    Returns True on success. Returns False — without raising and without
    touching the active pointer — when the id is unknown (neither
    registered nor backed by a `data/sandboxes/<id>.db` file) or malformed
    (non-string, empty, whitespace-only, bad characters), so the route can
    answer 404 directly off the return value."""
    if not isinstance(dataset_id, str) or not _DATASET_ID_RE.match(dataset_id):
        return False
    with _LOCK:
        known = dataset_id in _BY_ID or (SANDBOX_DIR / f"{dataset_id}.db").is_file()
        if not known:
            return False
        global _ACTIVE_ID
        _ACTIVE_ID = dataset_id
        return True


def get_active():
    """The current active dataset id, or None when no dataset is active."""
    with _LOCK:
        return _ACTIVE_ID


def active_path():
    """Filesystem path of the active dataset's SQLite file, or None when
    no dataset is active. Registered ids resolve to their registered path;
    scan-found orphans to `data/sandboxes/<id>.db`. Never creates files or
    directories; a missing file simply fails when a connection opens it."""
    active = get_active()
    if active is None:
        return None
    with _LOCK:
        entry = _BY_ID.get(active)
    if entry is not None:
        return Path(entry["path"])
    return SANDBOX_DIR / f"{active}.db"


def profile_db(path):
    """Profile one sandbox file: `{"tables", "rows", "data_end"}` where
    `tables` lists only the known event/account tables actually present
    (sorted), `rows` sums their row counts, and `data_end` is the
    union-MAX of `event_date` over the event tables present (None when no
    event date exists). Opens read-only (`mode=ro` URI) so profiling a
    missing path reports empty instead of creating a database file."""
    path = Path(path)
    empty = {"tables": [], "rows": 0, "data_end": None}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error:
        return dict(empty)
    try:
        try:
            present = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        except sqlite3.Error:
            return dict(empty)
        tables = sorted(name for name in _KNOWN_TABLES if name in present)
        rows = 0
        for table in tables:
            try:
                rows += conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                continue
        data_end = None
        event_tables = [name for name in _EVENT_TABLES if name in present]
        if event_tables:
            union = " UNION ALL ".join(
                f'SELECT MAX(event_date) AS d FROM "{name}"'
                for name in event_tables
            )
            try:
                data_end = conn.execute(
                    f"SELECT MAX(d) FROM ({union})"  # noqa: S608
                ).fetchone()[0]
            except sqlite3.Error:
                data_end = None
        return {"tables": tables, "rows": rows, "data_end": data_end}
    finally:
        conn.close()


def info(dataset_id):
    """The list entry for one dataset (`{"id", "source", "tables",
    "rows", "data_end"}`), or None when the id is neither registered nor
    backed by a sandbox file. Unregistered files report source "upload";
    a registered id whose file is missing profiles empty."""
    if not isinstance(dataset_id, str) or not _DATASET_ID_RE.match(dataset_id):
        return None
    with _LOCK:
        entry = _BY_ID.get(dataset_id)
        registered = dict(entry) if entry is not None else None
    path = Path(registered["path"]) if registered is not None else SANDBOX_DIR / f"{dataset_id}.db"
    if registered is None and not path.is_file():
        return None
    profile = profile_db(path)
    return {
        "id": dataset_id,
        "source": registered["source"] if registered is not None else "upload",
        "tables": profile["tables"],
        "rows": profile["rows"],
        "data_end": profile["data_end"],
    }


def list_datasets():
    """The `GET /data/datasets` body: `{"active_id", "datasets"}` with one
    `info()` entry per registered id plus every `data/sandboxes/*.db`
    orphan (source "upload"), sorted by id. The scan is live per call, so
    uploads appear with no registration step; files with unusable names
    are skipped. Never raises on an unreadable file or a missing
    directory — those entries profile empty."""
    with _LOCK:
        active = _ACTIVE_ID
        registered_ids = set(_BY_ID)
    try:
        stems = {path.stem for path in SANDBOX_DIR.glob("*.db")}
    except OSError:
        stems = set()
    ids = {
        dataset_id
        for dataset_id in (registered_ids | stems)
        if isinstance(dataset_id, str) and _DATASET_ID_RE.match(dataset_id)
    }
    entries = []
    for dataset_id in sorted(ids):
        entry = info(dataset_id)
        # A file removed between the glob and the profile reads as
        # unknown; dropping it keeps the list JSON-shaped, never null.
        if entry is not None:
            entries.append(entry)
    return {"active_id": active, "datasets": entries}
