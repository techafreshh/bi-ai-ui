"""Static contract tests for the committed sample CSVs (issue #72).

Stdlib-only file checks in the test_docker_contract.py style — no DB, no
daemon, no network. Headers pin against test_generate_data.EXPECTED_COLUMNS
(the generator contract's single source of truth); sizes, the >=20-cancels
/ duplicate-key / NULL-units minimums, checksum recomputation and README
provenance keywords enforce the issue's acceptance clauses directly.
"""

import csv
import hashlib
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from test_generate_data import EXPECTED_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = REPO_ROOT / "samples"
EXPORTER = REPO_ROOT / "agent" / "scripts" / "export_samples.py"
CSV_FILES = ("accounts", "subscription_events", "revenue_events", "usage_events")
ONE_MB = 1048576

RUN_TIMEOUT_SECONDS = 120


def _rows(name):
    with open(SAMPLES / f"{name}.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_csv_headers_match_generator_schema():
    """Every sample CSV header equals the generator contract's documented
    columns, in order (issue: 'exact SCHEMA columns')."""
    for name in CSV_FILES:
        assert name in EXPECTED_COLUMNS, name
        with open(SAMPLES / f"{name}.csv", newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        assert tuple(header) == EXPECTED_COLUMNS[name], name


def test_csv_sizes_under_1_mb():
    """Acceptance: total bytes of samples/*.csv under 1 MB and no single
    file over 1 MB (portable getsize instead of du)."""
    total = 0
    for name in CSV_FILES:
        size = (SAMPLES / f"{name}.csv").stat().st_size
        assert size <= ONE_MB, (name, size)
        total += size
    assert total < ONE_MB, total


def test_churn_cluster_has_at_least_20_cancels():
    """Acceptance: subscription_events.csv holds >=20 cancel rows, forming
    one repriced-churn cluster — a single 2026-08 window, all Growth, each
    a zero-delta marker with a paired in-window upgrade (the generator's
    churn semantics at miniature scale)."""
    events = _rows("subscription_events")
    accounts = {row["id"]: row for row in _rows("accounts")}
    cancels = [row for row in events if row["event_type"] == "cancel"]
    assert len(cancels) >= 20, len(cancels)
    assert {row["event_date"][:7] for row in cancels} == {"2026-08"}, (
        "one single-month cluster"
    )
    upgrades = {
        (row["account_id"], row["event_date"])
        for row in events if row["event_type"] == "upgrade"
    }
    for row in cancels:
        assert accounts[row["account_id"]]["segment"] == "Growth", row
        assert row["arr_delta_cents"] == "0", row  # the churn marker
        assert any(
            account_id == row["account_id"]
            and "2026-08-01" <= upgrade_date <= row["event_date"]
            for account_id, upgrade_date in upgrades
        ), row


def test_usage_dup_key_and_null_units():
    """Acceptance: usage_events.csv contains a duplicated
    (account_id,event_date,metric) key — an exact full-row duplicate — and
    a NULL (empty-field) units row, both confined to one segment's 7-day
    bug week."""
    events = _rows("usage_events")
    accounts = {row["id"]: row for row in _rows("accounts")}
    key_counts = Counter(
        (row["account_id"], row["event_date"], row["metric"]) for row in events
    )
    dup_keys = [key for key, count in key_counts.items() if count > 1]
    assert dup_keys, "no duplicated (account_id,event_date,metric) key"
    body_counts = Counter(
        tuple(row[column] for column in EXPECTED_COLUMNS["usage_events"] if column != "id")
        for row in events
    )
    assert any(count > 1 for count in body_counts.values()), (
        "duplicate key is not an exact full-row duplicate"
    )
    nulls = [row for row in events if row["units"] == ""]
    assert nulls, "no NULL (empty-field) units row"
    flagged = {
        (row["account_id"], row["event_date"])
        for row in events
        if (row["account_id"], row["event_date"], row["metric"]) in dup_keys
        or row["units"] == ""
    }
    segments = {accounts[account_id]["segment"] for account_id, _ in flagged}
    assert segments == {"Startup"}, segments
    days = sorted(day for _, day in flagged)
    assert min(days)[:10] >= "2026-09-08" and max(days)[:10] <= "2026-09-14", days
    span_days = (
        date.fromisoformat(max(days)[:10]) - date.fromisoformat(min(days)[:10])
    ).days
    assert span_days < 7, days


def test_sha256sums_match():
    """sha256sums.txt covers exactly the four CSVs with correct digests, so
    the acceptance's `sha256sum -c samples/sha256sums.txt` exits 0."""
    lines = (SAMPLES / "sha256sums.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(CSV_FILES), lines
    seen = set()
    for line in lines:
        digest, _, filename = line.partition("  ")
        assert len(digest) == 64 and all(
            char in "0123456789abcdef" for char in digest
        ), line
        target = (REPO_ROOT / filename).resolve()
        assert target.is_file(), line
        assert target.parent == SAMPLES.resolve(), line
        seen.add(target.name)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, line
    assert seen == {f"{name}.csv" for name in CSV_FILES}, seen
    if shutil.which("sha256sum") is not None:
        result = subprocess.run(
            ["sha256sum", "-c", "samples/sha256sums.txt"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_readme_documents_provenance():
    """README.md pins provenance: the generator script the shapes derive
    from, the exporter regen command, and the checksum verify command."""
    text = (SAMPLES / "README.md").read_text(encoding="utf-8")
    assert "generate_data.py" in text, "generator parity note"
    assert "export_samples.py" in text, "regen command"
    assert "sha256sum -c" in text, "verify command"
    assert "SQLite" in text or "data/app.db" in text, "no-SQLite note"


def test_no_sqlite_committed_in_samples():
    """No SQLite file is committed alongside the samples (issue: no
    committed SQLite >1MB — here: none at all)."""
    leftovers = [
        path.name
        for path in SAMPLES.iterdir()
        if path.suffix in (".db", ".sqlite", ".sqlite3") or "app.db" in path.name
    ]
    assert leftovers == [], leftovers


def test_account_arr_matches_subscription_deltas():
    """Miniature-scale mirror of the generator's bookkeeping invariant:
    accounts.arr_cents equals the per-account SUM(arr_delta_cents)."""
    accounts = {row["id"]: row for row in _rows("accounts")}
    totals = defaultdict(int)
    for row in _rows("subscription_events"):
        totals[row["account_id"]] += int(row["arr_delta_cents"])
    for account_id, account in accounts.items():
        assert totals[account_id] == int(account["arr_cents"]), account_id


def test_revenue_keys_unique_and_point_in_time():
    """Revenue is priced at the point-in-time ARR: exactly one row per
    (account_id,event_date) — each subscription point's segment ends the
    day before the next point (the iter_revenue_events parity), so upgrade
    days bill once at the new rate and cancel days bill once."""
    events = _rows("revenue_events")
    keys = [(row["account_id"], row["event_date"]) for row in events]
    assert len(keys) == len(set(keys)), "double-billed (account,day) key"
    accounts = {row["id"]: row for row in _rows("accounts")}
    subs = _rows("subscription_events")
    points = defaultdict(list)  # account_id -> sorted [(date, running_arr)]
    running = defaultdict(int)
    for row in sorted(subs, key=lambda row: (row["event_date"], row["id"])):
        running[row["account_id"]] += int(row["arr_delta_cents"])
        points[row["account_id"]].append(
            (row["event_date"], running[row["account_id"]])
        )
    price = {}
    for account_id, account_points in points.items():
        for index, (from_day, arr) in enumerate(account_points):
            if index + 1 < len(account_points):
                end_day = (
                    date.fromisoformat(account_points[index + 1][0])
                    - timedelta(days=1)
                ).isoformat()
            else:
                end_day = max(
                    row["event_date"]
                    for row in events
                    if row["account_id"] == account_id
                )
            day = from_day
            while day <= end_day:
                price.setdefault((account_id, day), str(arr // 365))
                day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    for row in events:
        assert row["amount_cents"] == price[
            (row["account_id"], row["event_date"])
        ], row
        assert accounts[row["account_id"]]["created_at"] <= row["event_date"], row
    cancels = {
        row["account_id"]: row["event_date"]
        for row in subs if row["event_type"] == "cancel"
    }
    for row in events:
        if row["account_id"] in cancels:
            assert row["event_date"] <= cancels[row["account_id"]], row


def test_exporter_rerun_reproduces_committed_samples(tmp_path):
    """The exporter entrypoint reruns deterministically: two fresh runs are
    byte-identical and match the committed CSVs."""
    first, second = tmp_path / "first", tmp_path / "second"
    for out_dir in (first, second):
        result = subprocess.run(
            [sys.executable, str(EXPORTER), "--out-dir", str(out_dir)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    for name in CSV_FILES:
        fresh = (first / f"{name}.csv").read_bytes()
        assert fresh == (second / f"{name}.csv").read_bytes(), name
        assert fresh == (SAMPLES / f"{name}.csv").read_bytes(), name
