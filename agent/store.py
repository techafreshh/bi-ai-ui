"""Deterministic seeded SQLite store for the demo data set.

Regeneration from the constants below always yields identical row content
(fixed RNG seed, fixed generation order), so tests and probe runs are
reproducible. The database file itself is a build artifact: gitignored under
the repo ``data/`` directory, created lazily by ``ensure_seeded()`` at agent
startup, and regenerated from scratch with
``python agent/scripts/generate_data.py --reset``.

Planted data shapes (PRD §7, tuned for the demo contract):

1. **Q3 growth (real)** — the Aug-1 2026 campaign lifts daily net-new ARR and
   also drives new-logo starts, so Q3 beats Q2 on net-new ARR, MRR and
   active accounts (NRR dips slightly because the churn spike lands in Q3
   too — that is the honest reading).
2. **Churn spike (real)** — exactly ``CHURN_ACCOUNTS`` Growth-segment accounts
   churn between the Aug-10 pricing change and Sep 30; their churned MRR
   dwarfs the trailing baseline, producing a large variance delta.
3. **Ingest bug (bad data)** — Startup-segment usage events between
   ``BUG_START`` and ``BUG_END`` get 8% NULL amounts plus exact duplicate
   copies; the window sits fully inside the trailing-30-day DQ window
   (horizon ``2026-10-13``) so the DQ read stays deterministic forever.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 42
SEED_VERSION = "bi-ai-ui-seed-1"

# Demo calendar constants. "Today" is pinned to the data horizon so windows,
# freshness and granularity never drift with the wall clock.
HORIZON = date(2026, 10, 13)
DATA_START = HORIZON - timedelta(days=730)  # 24 months of daily revenue events
Q3_START = date(2026, 7, 1)
Q3_END = date(2026, 9, 30)
Q2_START = date(2026, 4, 1)
Q2_END = date(2026, 6, 30)
CAMPAIGN_START = date(2026, 8, 1)  # Q3 growth driver
PRICING_CHANGE = date(2026, 8, 10)  # churn spike trigger
CHURN_ACCOUNTS = 42  # probe gate accepts 38–50 table rows
MIGRATION_BLIP = (date(2026, 4, 6), date(2026, 4, 19))  # pre-existing Q2 churn bump

# Ingest-bug window: two full weeks inside the trailing-30-day DQ window.
BUG_START = date(2026, 9, 15)
BUG_END = date(2026, 9, 28)
DQ_WINDOW_DAYS = 30  # trailing, horizon-inclusive

N_ACCOUNTS = 6400  # PRD: ~5–8k accounts
USAGE_HEAD_DAYS = 45  # usage events only need to cover the DQ window
MRR_START = 4_100_000.0

NULL_RATE = 0.08  # share of bug-window Startup events nulled
DUP_RATE = 0.6  # share of bug-window Startup events exact-copied

SEGMENT_WEIGHTS = {"Startup": 2560, "Growth": 2240, "Enterprise": 1600}
USAGE_RATE = {"Startup": 0.075, "Growth": 0.045, "Enterprise": 0.03}

_ADJECTIVES = [
    "Blue", "North", "Aster", "Cobalt", "Juniper", "Lumen", "Harbor", "Quartz",
    "Vertex", "Summit", "Delta", "Orbit", "Cedar", "Pilot", "Signal", "Beacon",
    "Ember", "Granite", "Helix", "Fieldstone",
]
_NOUNS = [
    "Analytics", "Systems", "Labs", "Works", "Digital", "Cloudware", "Metrics",
    "Dynamics", "Logistics", "Robotics", "Softworks", "Trading", "Health",
    "Grid", "Stack", "Loop", "Forge", "Logic", "Pulse", "Data",
]
_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
          "XI", "XII", "XIII", "XIV", "XV", "XVI"]


def db_path() -> Path:
    """Resolve the SQLite file location.

    ``BI_DATA_DIR`` overrides the default of ``<repo-root>/data/app.db`` so a
    single build artifact serves both host runs and containers.
    """
    root = os.getenv("BI_DATA_DIR")
    if root:
        return Path(root) / "app.db"
    return Path(__file__).resolve().parent.parent / "data" / "app.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the store with row access by name."""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _month_add(d: date, months: int) -> date:
    """Shift a date by whole months (clamping the day to month end)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    # Clamp forward through the month-end (28–31) so tenure stays in range.
    day = min(d.day, 28)
    return date(year, month, day)


def _account_name(i: int) -> str:
    base = f"{_ADJECTIVES[i % 20]} {_NOUNS[(i // 20) % 20]}"
    roman = _ROMAN[i // 400 + 1]
    return f"{base} {roman}".strip()


def _segment(i: int) -> str:
    if i < SEGMENT_WEIGHTS["Startup"]:
        return "Startup"
    if i < SEGMENT_WEIGHTS["Startup"] + SEGMENT_WEIGHTS["Growth"]:
        return "Growth"
    return "Enterprise"


def _plan(segment: str, cohort: bool) -> str:
    if cohort:
        # Cohort accounts sat on the pre-pricing Growth tier.
        return "Growth Legacy"
    return {"Startup": "Launch", "Growth": "Growth", "Enterprise": "Enterprise"}[segment]


def _assign_churn_dates(rnd: random.Random) -> list[date]:
    """Spread the CHURN_ACCOUNTS cohort evenly across the spike window."""
    span = (date(2026, 9, 30) - PRICING_CHANGE).days  # Aug 10 .. Sep 30
    return [PRICING_CHANGE + timedelta(days=(i * span) // CHURN_ACCOUNTS)
            for i in range(CHURN_ACCOUNTS)]


def _generate_accounts(rnd: random.Random) -> list[dict]:
    """Build the account book: cohort first so arr_daily can price the spike."""
    churn_dates = _assign_churn_dates(rnd)
    rows: list[dict] = []
    for i, churned in enumerate(churn_dates):
        tenure_months = 14 + (i * 7) % 34  # 14..47 months, deterministic mix
        started = _month_add(churned, -tenure_months)
        rows.append({
            "name": f"[Cohort] {_ADJECTIVES[i % 20]} {_NOUNS[(i * 3) % 20]}",
            "segment": "Growth",
            "plan": _plan("Growth", cohort=True),
            "mrr": 900 + rnd.randint(0, 60) * 25,  # 900..2400
            "started_at": started,
            "churned_at": churned,
            "cohort": True,
        })
    # Non-cohort book: 80% pre-existing, the rest new logos concentrated in the
    # campaign window so the Q3 accounts KPI shows the same lift as revenue.
    n_rest = N_ACCOUNTS - CHURN_ACCOUNTS
    for j in range(n_rest):
        i = CHURN_ACCOUNTS + j
        segment = _segment(i)
        draw = rnd.random()
        if draw < 0.20:  # new logos
            if draw < 0.12:  # campaign quarter drives most new logos
                day = CAMPAIGN_START + timedelta(days=rnd.randint(0, 60))
            else:
                day = DATA_START + timedelta(days=rnd.randint(0, 700))
            started = min(day, HORIZON - timedelta(days=1))
        else:
            started = DATA_START - timedelta(days=rnd.randint(0, 540))
        mrr_range = {"Startup": (80, 400), "Growth": (300, 2500),
                     "Enterprise": (2000, 20000)}[segment]
        step = {"Startup": 5, "Growth": 10, "Enterprise": 50}[segment]
        mrr = rnd.randint(mrr_range[0] // step, mrr_range[1] // step) * step
        rows.append({
            "name": _account_name(i),
            "segment": segment,
            "plan": _plan(segment, cohort=False),
            "mrr": float(mrr),
            "started_at": started,
            "churned_at": None,
            "cohort": False,
        })
    return rows


def _generate_arr_daily(rnd: random.Random, rows: list[dict]) -> list[dict]:
    """Daily revenue events: baseline, campaign lift, churn cohort, Q2 blip."""
    cohort_by_day: dict[date, float] = {}
    for row in rows:
        if row["cohort"]:
            cohort_by_day[row["churned_at"]] = cohort_by_day.get(row["churned_at"], 0.0) + row["mrr"]

    # Active accounts per day from interval membership. The pre-existing book
    # is credited on day one instead of scrolling in through start dates.
    adds: dict[date, int] = {}
    drops: dict[date, int] = {}
    for row in rows:
        if row["started_at"] >= DATA_START:
            adds[row["started_at"]] = adds.get(row["started_at"], 0) + 1
        if row["churned_at"]:
            drops[row["churned_at"]] = drops.get(row["churned_at"], 0) + 1
    active = sum(1 for r in rows if r["started_at"] < DATA_START)

    daily = []
    closing = MRR_START
    n_days = (HORIZON - DATA_START).days + 1
    for offset in range(n_days):
        day = DATA_START + timedelta(days=offset)
        active += adds.get(day, 0) - drops.get(day, 0)
        base_new = 1400 + rnd.randint(0, 400)
        if day >= CAMPAIGN_START:
            base_new += 2000 + rnd.randint(0, 900)
        churned = 1500 + rnd.randint(0, 400)
        if MIGRATION_BLIP[0] <= day <= MIGRATION_BLIP[1]:
            churned = int(churned * 1.5)  # pre-existing Q2 churn bump keeps Q2 NRR low
        churned += cohort_by_day.get(day, 0)
        new_arr, churned_arr = float(base_new), float(churned)
        closing += new_arr - churned_arr
        daily.append({
            "day": day,
            "new_arr": new_arr,
            "churned_arr": churned_arr,
            "closing_mrr": closing,
            "active_accounts": active,
        })
    return daily


def _generate_usage(rnd: random.Random, rows: list[dict]) -> list[dict]:
    """Usage events over a bounded head window (only DQ reads them).

    Bug injection: Startup events in BUG_START..BUG_END get NULL amounts at
    NULL_RATE and exact duplicate copies at DUP_RATE (copies inherit the NULL
    amount, which is exactly what an ingest bug looks like in a warehouse).
    """
    active = [row for row in rows if row["started_at"] <= HORIZON - timedelta(days=USAGE_HEAD_DAYS)]
    usage_start = HORIZON - timedelta(days=USAGE_HEAD_DAYS - 1)
    events: list[dict] = []

    def add(account_idx: int, ts: str, amount: float | None) -> None:
        events.append({
            "account_idx": account_idx,
            "ts": ts,
            "metric": "api_calls",
            "amount": amount,
        })

    for day_offset in range(USAGE_HEAD_DAYS):
        day = usage_start + timedelta(days=day_offset)
        in_bug_window = BUG_START <= day <= BUG_END
        for row_idx, row in enumerate(active):
            if rnd.random() >= USAGE_RATE[row["segment"]]:
                continue
            # Late-day events on the horizon keep freshness green with day-age 0,
            # but never past early evening of "today".
            hh = rnd.randint(0, 18) if day == HORIZON else rnd.randint(0, 23)
            mm = rnd.randint(0, 59) if day != HORIZON else rnd.randint(0, 30)
            ts = f"{day.isoformat()}T{hh:02d}:{mm:02d}:00"
            amount = round(rnd.uniform(0.5, 500.0), 2)
            nulled = in_bug_window and row["segment"] == "Startup" and rnd.random() < NULL_RATE
            copied = in_bug_window and row["segment"] == "Startup" and rnd.random() < DUP_RATE
            row_amount = None if nulled else amount
            add(row_idx, ts, row_amount)
            if copied:
                # Exact duplicate of the row just emitted (same amount / ts).
                add(row_idx, ts, row_amount)
    return events


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  segment TEXT NOT NULL,
  plan TEXT NOT NULL,
  mrr REAL NOT NULL,
  started_at TEXT NOT NULL,
  churned_at TEXT
);
CREATE TABLE IF NOT EXISTS arr_daily (
  day TEXT PRIMARY KEY,
  new_arr REAL NOT NULL,
  churned_arr REAL NOT NULL,
  closing_mrr REAL NOT NULL,
  active_accounts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  ts TEXT NOT NULL,
  metric TEXT NOT NULL,
  amount REAL
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_accounts_churned ON accounts(churned_at);
"""


def build_rows() -> tuple[list[dict], list[dict], list[dict]]:
    """Generate the full data set in memory (deterministic, seed 42)."""
    rnd = random.Random(SEED)
    accounts = _generate_accounts(rnd)
    arr_daily = _generate_arr_daily(rnd, accounts)
    usage = _generate_usage(rnd, accounts)
    return accounts, arr_daily, usage


def write_db(path: Path) -> dict:
    """Create the schema and write the generated rows, returning counts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    accounts, arr_daily, usage = build_rows()
    conn = connect(path)
    try:
        with conn:
            conn.executescript(SCHEMA)
            conn.execute("DELETE FROM meta")
            conn.execute("INSERT INTO meta (key, value) VALUES ('seed_version', ?)", (SEED_VERSION,))
            conn.execute("DELETE FROM usage_events")
            conn.execute("DELETE FROM arr_daily")
            conn.execute("DELETE FROM accounts")
            conn.executemany(
                "INSERT INTO accounts (id, name, segment, plan, mrr, started_at, churned_at) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                [(r["name"], r["segment"], r["plan"], r["mrr"],
                  r["started_at"].isoformat(),
                  r["churned_at"].isoformat() if r["churned_at"] else None) for r in accounts],
            )
            conn.executemany(
                "INSERT INTO arr_daily VALUES (?, ?, ?, ?, ?)",
                [(r["day"].isoformat(), r["new_arr"], r["churned_arr"],
                  r["closing_mrr"], r["active_accounts"]) for r in arr_daily],
            )
            conn.executemany(
                "INSERT INTO usage_events (account_id, ts, metric, amount) VALUES (?, ?, ?, ?)",
                [(r["account_idx"] + 1, r["ts"], r["metric"], r["amount"]) for r in usage],
            )
        counts = {
            "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "arr_daily": conn.execute("SELECT COUNT(*) FROM arr_daily").fetchone()[0],
            "usage_events": conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0],
            "churn_cohort": conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE churned_at IS NOT NULL").fetchone()[0],
        }
        return counts
    finally:
        conn.close()


def ensure_seeded(reset: bool = False) -> dict:
    """Seed the store on first use (called by the server at startup).

    Skips the rebuild when a database with a matching ``seed_version`` already
    exists; ``reset=True`` forces regeneration. Returns the table counts.
    """
    path = db_path()
    if not reset and path.exists():
        try:
            conn = connect(path)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = 'seed_version'").fetchone()
            finally:
                conn.close()
            if row and row["value"] == SEED_VERSION:
                conn = connect(path)
                try:
                    return {
                        "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
                        "arr_daily": conn.execute("SELECT COUNT(*) FROM arr_daily").fetchone()[0],
                        "usage_events": conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0],
                        "churn_cohort": conn.execute(
                            "SELECT COUNT(*) FROM accounts WHERE churned_at IS NOT NULL").fetchone()[0],
                    }
                finally:
                    conn.close()
        except sqlite3.DatabaseError:
            pass  # unreadable file: fall through and rebuild
    return write_db(path)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (``agent/scripts/generate_data.py`` shim)."""
    parser = argparse.ArgumentParser(description="Generate the deterministic demo database.")
    parser.add_argument("--reset", action="store_true", help="rebuild even if a seed exists")
    parser.add_argument("--db", help="override the output path (defaults to <repo>/data/app.db)")
    args = parser.parse_args(argv)
    if args.db:
        os.environ["BI_DATA_DIR"] = str(Path(args.db).parent)
        path = Path(args.db)
        counts = write_db(path)
    else:
        counts = ensure_seeded(reset=args.reset)
        path = db_path()
    print(f"seeded {path}: {counts}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
