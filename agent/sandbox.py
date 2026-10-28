"""CSV-to-sandbox SQLite loader (issue #73).

Uploads and samples are isolated per dataset: `load_csv` imports one CSV
table into a fresh `data/sandboxes/<id>.db` file that always carries the
full four-table schema plus the three `event_date` indexes, so queries
against any sandbox observe the same shape as the seeded `data/app.db`.
Dataset ids are restricted to `[A-Za-z0-9_-]+` (a `../` escape is
ValueError, never a path) and an existing same-id file is unlinked first —
rerun idempotence mirroring `scripts/generate_data.py:main`.

Type coercion is strict and happens before any write: integer columns parse
via `int()` (empty string reads NULL only for the nullable `units`
column, otherwise ValueError), date columns are canonicalized through the
`queries.py:_require_date` idiom (`date.fromisoformat`, ValueError on bad
input), and an unknown table/header or an empty file raises ValueError
without creating any `.db` file.

Stdlib only: `csv`, `re`, `sqlite3` and `pathlib`, so importing this module
never touches the agent's model dependencies.
"""

import csv
import re
import sqlite3
from datetime import date
from pathlib import Path

try:  # package-style entry (import agent.sandbox from the repo root)
    from agent import db
except ModuleNotFoundError:  # script-style entry (import sandbox from agent/)
    import db

# Only letters, digits, `_` and `-`: `<id>.db` can never escape the sandbox
# directory, so `../evil` and absolute paths are rejected, not resolved.
_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Per-table columns in SCHEMA_SQL order; the CSV header must equal the
# table's tuple exactly (order included) or the load is rejected.
_TABLE_COLUMNS = {
    "accounts": (
        "id",
        "name",
        "segment",
        "plan",
        "arr_cents",
        "created_at",
    ),
    "subscription_events": (
        "id",
        "account_id",
        "event_date",
        "event_type",
        "arr_delta_cents",
    ),
    "revenue_events": (
        "id",
        "account_id",
        "event_date",
        "amount_cents",
    ),
    "usage_events": (
        "id",
        "account_id",
        "event_date",
        "metric",
        "units",
    ),
}

# Columns coerced with int(); every other column stays TEXT (dates pass
# through fromisoformat canonicalization instead).
_INTEGER_COLUMNS = {
    "accounts": frozenset({"id", "arr_cents"}),
    "subscription_events": frozenset({"id", "account_id", "arr_delta_cents"}),
    "revenue_events": frozenset({"id", "account_id", "amount_cents"}),
    "usage_events": frozenset({"id", "account_id", "units"}),
}

# Date columns validated via the queries.py:_require_date idiom.
_DATE_COLUMNS = {
    "accounts": frozenset({"created_at"}),
    "subscription_events": frozenset({"event_date"}),
    "revenue_events": frozenset({"event_date"}),
    "usage_events": frozenset({"event_date"}),
}

# The full four-table schema plus the three event_date indexes, copied
# verbatim from scripts/generate_data.py:SCHEMA_SQL (the single source of
# truth for table shape) — the loader always builds all of it even though
# each load imports only one CSV table, so every sandbox exposes the same
# indexes the acceptance probe queries.
SANDBOX_SCHEMA_SQL = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL,
    plan TEXT NOT NULL,
    arr_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE subscription_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    arr_delta_cents INTEGER NOT NULL
);
CREATE TABLE revenue_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL
);
CREATE TABLE usage_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    event_date TEXT NOT NULL,
    metric TEXT NOT NULL,
    units INTEGER
);
CREATE INDEX idx_subscription_events_event_date ON subscription_events(event_date);
CREATE INDEX idx_revenue_events_event_date ON revenue_events(event_date);
CREATE INDEX idx_usage_events_event_date ON usage_events(event_date);
"""


def _require_dataset_id(dataset_id):
    """Validate the sandbox id; anything outside `[A-Za-z0-9_-]+` (notably
    `../` escapes and absolute paths) is ValueError before any I/O."""
    if not isinstance(dataset_id, str) or not _DATASET_ID_RE.match(dataset_id):
        raise ValueError(
            f"dataset_id must match [A-Za-z0-9_-]+, got {dataset_id!r}"
        )
    return dataset_id


def _require_table(table):
    """The CSV stem (or explicit override) must name one of the four
    schema tables; anything else is ValueError."""
    if table not in _TABLE_COLUMNS:
        raise ValueError(
            f"unknown table {table!r}; must be one of {sorted(_TABLE_COLUMNS)}"
        )
    return table


def _coerce_value(table, column, value):
    """Coerce one raw CSV field: integers via `int()` (`units` alone maps
    `""` to NULL), dates via `date.fromisoformat` canonicalization, TEXT
    columns verbatim. ValueError on anything unparseable."""
    if value is None:
        value = ""
    if column in _INTEGER_COLUMNS[table]:
        if value == "":
            if column == "units":
                return None
            raise ValueError(f"column {column!r} must not be empty")
        try:
            return int(value)
        except ValueError:
            raise ValueError(
                f"column {column!r} is not an integer: {value!r}"
            ) from None
    if column in _DATE_COLUMNS[table]:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError(
                f"column {column!r} is not an ISO date: {value!r}"
            ) from None
    return value


def load_table(table, rows, dataset_id, sandbox_dir=None):
    """Write already-coerced `rows` (a list of tuples in the table's column
    order) into a fresh `data/sandboxes/<dataset_id>.db` carrying the full
    schema and the three `event_date` indexes. Returns the sandbox path.

    An existing same-id file is unlinked first (rerun idempotence); the
    parent directory is created. `table` must be a known schema table and
    every row must match its arity, or ValueError.
    """
    _require_dataset_id(dataset_id)
    _require_table(table)
    columns = _TABLE_COLUMNS[table]
    for row in rows:
        if len(row) != len(columns):
            raise ValueError(
                f"table {table!r} expects {len(columns)} values, "
                f"got {len(row)}"
            )
    directory = Path(sandbox_dir) if sandbox_dir is not None else db.SANDBOX_DIR
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{dataset_id}.db"
    # Unlink first so reruns over an existing id are idempotent instead of
    # failing on the precreated schema or appending duplicate rows.
    if dest.exists():
        dest.unlink()
    placeholders = ", ".join(["?"] * len(columns))
    conn = sqlite3.connect(str(dest), timeout=10.0)
    try:
        conn.executescript(SANDBOX_SCHEMA_SQL)
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return dest


def load_csv(csv_path, dataset_id, table=None, sandbox_dir=None):
    """Import one CSV table into a fresh sandbox DB (issue #73).

    csv_path: the CSV file; table defaults to the file's stem
        (`accounts.csv` loads the `accounts` table). The header must equal
        the table's columns exactly and the file must hold at least one
        data row — unknown tables/columns, bad values and empty files raise
        ValueError BEFORE any file is created. Returns the sandbox path
        (`<sandbox_dir>/<dataset_id>.db`, defaulting to db.SANDBOX_DIR).
    """
    _require_dataset_id(dataset_id)
    source = Path(csv_path)
    resolved_table = _require_table(
        table if table is not None else source.stem
    )
    columns = _TABLE_COLUMNS[resolved_table]
    with open(source, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"{source} is empty: missing header row")
        if tuple(header) != columns:
            raise ValueError(
                f"{source} header {header!r} does not match "
                f"table {resolved_table!r} columns {list(columns)!r}"
            )
        rows = []
        for record in reader:
            if len(record) != len(columns):
                raise ValueError(
                    f"{source} row {reader.line_num} has {len(record)} "
                    f"fields, expected {len(columns)}"
                )
            rows.append(
                tuple(
                    _coerce_value(resolved_table, column, value)
                    for column, value in zip(columns, record)
                )
            )
    if not rows:
        raise ValueError(f"{source} holds no data rows")
    return load_table(resolved_table, rows, dataset_id, sandbox_dir)


def main(argv=None):
    """CLI: `python sandbox.py <csv_path> <dataset_id> [table]` — prints the
    created sandbox path. Mirrors the subprocess-entrypoint style the suite
    drives for the generator (`cwd=agent/`, real file, no fixtures)."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2, 3):
        raise SystemExit("usage: sandbox.py <csv_path> <dataset_id> [table]")
    csv_path, dataset_id = args[0], args[1]
    table = args[2] if len(args) == 3 else None
    print(load_csv(csv_path, dataset_id, table))


if __name__ == "__main__":
    main()
