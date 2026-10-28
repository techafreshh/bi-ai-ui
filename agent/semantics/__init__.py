"""Metric registry loader for the semantics file (issue #77).

`agent/semantics/metrics.yaml` declares the eight queryable metrics in a
flat list-of-mappings schema (one mapping per `- name:` entry, scalar
`key: value` lines only). This module parses that file with a purpose-built
stdlib parser — no PyYAML dependency — and validates every entry at load:
required keys first, then column membership, then name membership, so a
bogus entry carrying both faults raises `ValueError` containing
`unknown column` before any query runs.

`load_metrics()` returns exactly `[{name, definition, grain, format}]` in
file order (the public shape the acceptance clause pins); `_records()`
returns the full validated rows including `column`/`owner` for the
`queries.py` compiler. `queries.py` calls `load_metrics()` at module level,
so a bad file raises at import and the server refuses to boot on it.

Stdlib only, mirroring db.py: resolved from `__file__`, never the cwd.
"""

from pathlib import Path

# Sibling registry file — resolved from this file's location, never from
# the cwd, so every entrypoint (script, test, server) reads the same file
# no matter where it is launched from.
METRICS_FILE = Path(__file__).resolve().parent / "metrics.yaml"

# Union of every column in SCHEMA_SQL (agent/scripts/generate_data.py):
# accounts(id/name/segment/plan/arr_cents/created_at),
# subscription_events(id/account_id/event_date/event_type/arr_delta_cents),
# revenue_events(id/account_id/event_date/amount_cents),
# usage_events(id/account_id/event_date/metric/units).
KNOWN_COLUMNS = frozenset(
    {
        "id",
        "name",
        "segment",
        "plan",
        "arr_cents",
        "created_at",
        "account_id",
        "event_date",
        "event_type",
        "arr_delta_cents",
        "amount_cents",
        "metric",
        "units",
    }
)

# The eight compiler-known metrics in metrics.yaml file order (PRDv2
# section 7.5 six first, then the two file-only additions). Anything else
# in the file is a typo and raises `unknown metric`.
_METRIC_NAMES = (
    "net_new_arr",
    "mrr",
    "nrr",
    "churn_rate",
    "active_accounts",
    "data_quality",
    "gross_churned_arr",
    "active_accounts_eom",
)

_REQUIRED_KEYS = ("name", "definition", "grain", "format", "column", "owner")
_PUBLIC_KEYS = ("name", "definition", "grain", "format")


def _strip_quotes(value):
    """Remove one layer of matching single/double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _strip_inline_comment(value):
    """Cut a ` #` comment starting outside single/double quotes."""
    quote = None
    for i, char in enumerate(value):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and i > 0 and value[i - 1] in (" ", "\t"):
            return value[:i].rstrip()
    return value


def _parse_key_value(text, lineno, raw):
    """Split one `key: value` line; anything else is a ValueError."""
    if ":" not in text:
        raise ValueError(
            f"metrics.yaml line {lineno}: expected 'key: value', got {raw!r}"
        )
    key, value = text.split(":", 1)
    key = key.strip()
    value = _strip_quotes(_strip_inline_comment(value.strip()))
    if not key:
        raise ValueError(
            f"metrics.yaml line {lineno}: empty key in {raw!r}"
        )
    return key, value


def _parse_simple_yaml(text):
    """Parse the flat list-of-mappings schema: `- ` items and indented
    `key: value` lines, `#` full-line comments and blank lines skipped,
    `---`/`...` document markers ignored. Anything else raises ValueError
    so typos fail fast instead of loading silently."""
    records = []
    current = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
            continue
        if stripped.startswith("-"):
            rest = stripped[1:]
            if rest != "" and not rest.startswith((" ", "\t")):
                raise ValueError(
                    f"metrics.yaml line {lineno}: malformed entry {raw!r}"
                )
            rest = rest.strip()
            current = {}
            records.append(current)
            if not rest:
                continue
            key, value = _parse_key_value(rest, lineno, raw)
            if key in current:
                raise ValueError(
                    f"metrics.yaml line {lineno}: duplicate key {key!r}"
                )
            current[key] = value
        else:
            if current is None:
                raise ValueError(
                    f"metrics.yaml line {lineno}: expected a '- ' entry, "
                    f"got {raw!r}"
                )
            if raw[0] not in (" ", "\t"):
                raise ValueError(
                    f"metrics.yaml line {lineno}: expected a '- ' entry, "
                    f"got {raw!r}"
                )
            key, value = _parse_key_value(stripped, lineno, raw)
            if key in current:
                raise ValueError(
                    f"metrics.yaml line {lineno}: duplicate key {key!r}"
                )
            current[key] = value
    return records


def _records(path=None):
    """Full validated rows (all six keys) in file order. Raises ValueError
    on the first bad entry: missing/unknown keys, then unknown column,
    then unknown metric, then invalid owner — before any query runs."""
    target = Path(path) if path is not None else METRICS_FILE
    parsed = _parse_simple_yaml(target.read_text(encoding="utf-8"))
    full = []
    for index, record in enumerate(parsed):
        label = record.get("name", f"entry #{index + 1}")
        missing = [key for key in _REQUIRED_KEYS if key not in record]
        if missing:
            raise ValueError(
                f"metric {label!r} is missing required keys: "
                + ", ".join(missing)
            )
        extra = [key for key in record if key not in _REQUIRED_KEYS]
        if extra:
            raise ValueError(
                f"metric {label!r} has unknown fields: " + ", ".join(extra)
            )
        for key in _REQUIRED_KEYS:
            if not isinstance(record[key], str) or not record[key].strip():
                raise ValueError(f"metric {label!r} has an empty value for {key!r}")
        column = record["column"]
        if column not in KNOWN_COLUMNS:
            raise ValueError(
                f"unknown column {column!r} in metric {record['name']!r}"
            )
        if record["name"] not in _METRIC_NAMES:
            raise ValueError(
                f"unknown metric {record['name']!r}; "
                f"must be one of {list(_METRIC_NAMES)}"
            )
        owner = record["owner"]
        if not (owner.startswith("queries.") or owner.startswith("quality.")):
            raise ValueError(
                f"metric {record['name']!r} has an invalid owner {owner!r}; "
                "must start with 'queries.' or 'quality.'"
            )
        full.append(dict(record))
    return full


def load_metrics(path=None):
    """Load the registry: exactly [{name, definition, grain, format}] per
    entry, in file order. `path` overrides METRICS_FILE (tests pass a
    tampered copy); anything invalid raises ValueError."""
    return [{key: record[key] for key in _PUBLIC_KEYS} for record in _records(path)]
