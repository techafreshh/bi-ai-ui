"""Deterministic seeded SQLite data generator (issue #3).

Running `python agent/scripts/generate_data.py` creates `data/app.db`
(gitignored) with mock B2B-SaaS data covering the 24 months ending
2026-09-30: ~5–8k accounts plus daily subscription / revenue / usage event
tables, all reproducible from one fixed seed — running the script twice
yields identical per-table row counts and identical total revenue to the
cent. An Aug-1-2026 go-to-market campaign is baked in as pinned monthly
new-account counts, producing smooth, strong net-new ARR growth through
Q3 2026.

Schema (created verbatim by this script; dates are TEXT 'YYYY-MM-DD' so SQL
and probes filter them naturally, money is INTEGER cents — never floats):

    accounts(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,            -- unique combo-word company name
        segment TEXT NOT NULL,         -- 'Startup' | 'Growth' | 'Enterprise'
        plan TEXT NOT NULL,            -- 'Starter' | 'Growth' | 'Scale' (current ladder rung)
        arr_cents INTEGER NOT NULL,    -- == SUM(arr_delta_cents) of the account's subscription events
        created_at TEXT NOT NULL);     -- first day the account is active

    subscription_events(
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        event_date TEXT NOT NULL,
        event_type TEXT NOT NULL,      -- 'new' | 'upgrade' ('cancel' reserved for a later issue)
        arr_delta_cents INTEGER NOT NULL);

    revenue_events(
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        event_date TEXT NOT NULL,
        amount_cents INTEGER NOT NULL);

    usage_events(
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        event_date TEXT NOT NULL,
        metric TEXT NOT NULL,          -- always 'api_calls' in this issue
        units INTEGER NOT NULL);

Net-new ARR for a month := SUM(arr_delta_cents) over that month's
subscription_events. The 2024-10 founding cohort is pinned to 2024-10-01,
which pins the minimum event date inside the acceptance window
2024-10-01..2024-10-31; every account bills daily pro-rata through
2026-09-30, so that exact day is always the maximum event date.

Growth shape: baseline months draw 210..270 new accounts; the campaign
months pin 2026-07: 280, 2026-08: 560, 2026-09: 840 (2× and 3× July). With
the weighted plan mix (E[ARR] ≈ $7,710 per new account), Jul/Aug/Sep net-new
ARR land near $2.2M / $4.3M / $6.5M — each month ≥ the prior one and
Aug+Sep ≥ 1.5× Jul with sampling noise roughly an order of magnitude below
the ramp steps.

Determinism is structural, not hoped for: all randomness flows through ONE
`random.Random(SEED)` passed to pure builders that consume draws in a fixed
order (monthly counts → per-account plan/segment/day/name → monthly upgrade
picks → usage draws). Do not insert new draws above existing ones. No
module-global `random`, no iteration over sets (PYTHONHASHSEED
independence), sequential ids, integer-only money math.
"""

import argparse
import calendar
import random
import sys
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

# scripts/ is not a package: put agent/ on sys.path so `import db` works no
# matter which cwd the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402  (import must follow the sys.path bootstrap above)

SEED = 20261020

FIRST_YEAR, FIRST_MONTH_NUM = 2024, 10
FIRST_MONTH_KEY = f"{FIRST_YEAR:04d}-{FIRST_MONTH_NUM:02d}"  # founding cohort month
MONTHS_COVERED = 24                # exactly 2024-10 .. 2026-09
END_DATE = date(2026, 9, 30)       # hard last day of data (max event date)

# Plan ladder (single step up) and its ARR deltas, in integer cents.
PLANS = {"Starter": 180_000, "Growth": 600_000, "Scale": 2_400_000}
NEXT_PLAN = {"Starter": "Growth", "Growth": "Scale"}
UPGRADE_DELTA_CENTS = {
    "Starter": PLANS["Growth"] - PLANS["Starter"],   # 420_000
    "Growth": PLANS["Scale"] - PLANS["Growth"],      # 1_800_000
}

# Weighted draw tables for the monthly new-account cohort; E[ARR] =
# 0.45*1800 + 0.35*6000 + 0.20*24000 = $7,710 per new account.
PLAN_MIX = (("Starter", 0.45), ("Growth", 0.35), ("Scale", 0.20))
SEGMENT_MIX = (("Startup", 0.45), ("Growth", 0.35), ("Enterprise", 0.20))

# Aug-1-2026 campaign: pinned cohort sizes (2× and 3× July's 280).
CAMPAIGN_MONTH_COUNTS = {"2026-07": 280, "2026-08": 560, "2026-09": 840}
BASELINE_NEW_COUNT_RANGE = (210, 270)  # non-campaign months draw here

ONE_IN_NEW_ACCOUNTS_UPGRADE = 25   # upgrades per month ≈ new_count // 25
USAGE_PROB = 0.35                  # chance an active account-day emits usage
USAGE_METRIC = "api_calls"
USAGE_UNITS_RANGE = (120, 50_000)
WEEKEND_USAGE_PCT = 40             # weekend usage factor, integer percent
DAILY_REVENUE_DAYS = 365           # pro-rata divisor (constant, integer math)

NAME_PREFIXES = ("Azure", "Borealis", "Cinder", "Dune", "Ember", "Fjord", "Grove", "Halcyon")
NAME_SUFFIXES = (
    "Nanometrics", "Dynamics", "Labs", "Systems", "Works",
    "Analytics", "Robotics", "Digital", "Cloudworks", "Logic",
)
NAME_ATTEMPTS = 1000               # bounded dedupe retries before the serial fallback

INSERT_BATCH_ROWS = 100_000        # executemany chunk size, bounds memory

TABLE_NAMES = ("accounts", "subscription_events", "revenue_events", "usage_events")
EVENT_TABLE_NAMES = ("subscription_events", "revenue_events", "usage_events")

SCHEMA_SQL = """
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
    units INTEGER NOT NULL
);
CREATE INDEX idx_subscription_events_event_date ON subscription_events(event_date);
CREATE INDEX idx_revenue_events_event_date ON revenue_events(event_date);
CREATE INDEX idx_usage_events_event_date ON usage_events(event_date);
"""


def plan_months(rng):
    """Return [(month_key, new_count)] for the 24 covered months.

    Campaign months take pinned CAMPAIGN_MONTH_COUNTS (no draw); baseline
    months draw inside BASELINE_NEW_COUNT_RANGE, in chronological order.
    """
    plan = []
    year, month = FIRST_YEAR, FIRST_MONTH_NUM
    for _ in range(MONTHS_COVERED):
        key = f"{year:04d}-{month:02d}"
        pinned = CAMPAIGN_MONTH_COUNTS.get(key)
        count = pinned if pinned is not None else rng.randint(*BASELINE_NEW_COUNT_RANGE)
        plan.append((key, count))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return plan


def _weighted(rng, mix):
    """Draw one kind from a ((kind, cumulative share), ...) table."""
    draw = rng.random()
    cumulative = 0.0
    for kind, share in mix:
        cumulative += share
        if draw < cumulative:
            return kind
    return mix[-1][0]


def _unique_name(rng, used_names):
    """Draw a combo-word company name until unused (bounded retries)."""
    for _ in range(NAME_ATTEMPTS):
        name = (
            f"{rng.choice(NAME_PREFIXES)} {rng.choice(NAME_SUFFIXES)} "
            f"{rng.randint(1, 999):03d}"
        )
        if name not in used_names:
            used_names.add(name)
            return name
    # Deterministic fallback after the bounded retries: serialised on the
    # dedupe set size, which grows monotonically, so it never collides.
    name = f"Reserve Analytics {len(used_names):05d}"
    used_names.add(name)
    return name


def build_accounts(rng, plan):
    """Create all accounts and subscription events in strict month order.

    Returns (accounts, subscription_events): accounts is a list of dicts
    carrying FINAL plan/arr_cents (upgrades applied before insert);
    subscription_events is a list of tuples
    (id, account_id, event_date, event_type, arr_delta_cents) in
    chronological order. Draw order per month: each new account's plan,
    segment, created-day and name, then that month's upgrade picks and days.
    """
    accounts = []
    subscription_events = []
    used_names = set()  # membership only — never iterated (hash-order safe)
    next_account_id = 1
    next_event_id = 1
    prior_account_count = 0
    for month_key, new_count in plan:
        year, month = map(int, month_key.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        founding = month_key == FIRST_MONTH_KEY
        for _ in range(new_count):
            plan_name = _weighted(rng, PLAN_MIX)
            segment = _weighted(rng, SEGMENT_MIX)
            # The founding cohort is pinned to day 1 so the minimum event
            # date is deterministically inside the acceptance window.
            day = 1 if founding else rng.randint(1, last_day)
            name = _unique_name(rng, used_names)
            event_date = f"{month_key}-{day:02d}"
            arr_cents = PLANS[plan_name]
            accounts.append({
                "id": next_account_id,
                "name": name,
                "segment": segment,
                "plan": plan_name,
                "arr_cents": arr_cents,
                "created_at": event_date,
            })
            subscription_events.append(
                (next_event_id, next_account_id, event_date, "new", arr_cents)
            )
            next_account_id += 1
            next_event_id += 1
        # Small expansion tail: a few prior-month accounts climb one ladder
        # rung. Scaled by the month's cohort size, this keeps the Q3 shape
        # untouched (the campaign months simply upgrade more, too).
        upgrade_count = new_count // ONE_IN_NEW_ACCOUNTS_UPGRADE
        if upgrade_count and prior_account_count:
            chosen = set()  # ids already upgraded this month, membership only
            for _ in range(upgrade_count):
                for _ in range(NAME_ATTEMPTS):
                    index = rng.randrange(prior_account_count)
                    account = accounts[index]
                    if index not in chosen and account["plan"] in UPGRADE_DELTA_CENTS:
                        break
                else:
                    continue  # retries exhausted — accept a smaller tail
                chosen.add(index)
                day = rng.randint(1, last_day)
                old_plan = account["plan"]
                account["plan"] = NEXT_PLAN[old_plan]
                account["arr_cents"] += UPGRADE_DELTA_CENTS[old_plan]
                subscription_events.append((
                    next_event_id, account["id"], f"{month_key}-{day:02d}",
                    "upgrade", UPGRADE_DELTA_CENTS[old_plan],
                ))
                next_event_id += 1
        prior_account_count += new_count
    return accounts, subscription_events


def _account_timelines(accounts, subscription_events):
    """Per account (indexed by id - 1): date-sorted [(from_date, arr_cents)].

    Running totals over the account's subscription events, used to price
    daily revenue at the point-in-time ARR.
    """
    events_by_account = {}  # access is by key only; the dict is not iterated
    for event in subscription_events:  # list — chronological
        events_by_account.setdefault(event[1], []).append(event)
    accounts = sorted(accounts, key=lambda account: account["id"])  # list sort — stable
    timelines = [None] * len(accounts)
    for account in accounts:
        arr_cents = 0
        points = []
        for _event_id, _account_id, event_date, _type, delta in sorted(
            events_by_account.get(account["id"], [])
        ):  # ids are chronological, so tuple sort == date order
            arr_cents += delta
            points.append((date.fromisoformat(event_date), arr_cents))
        timelines[account["id"] - 1] = points
    return timelines


def iter_revenue_events(accounts, timelines):
    """Yield (account_id, event_date, amount_cents) for every active day.

    One pro-rata row per account-day (|created_at, END_DATE|) priced at the
    point-in-time ARR // DAILY_REVENUE_DAYS (floor, integers only) — daily
    event tables per the issue, and every date in the window gets rows so
    the min/max acceptance dates hold by construction.
    """
    for account in accounts:
        points = timelines[account["id"] - 1]
        for index, (from_date, arr_cents) in enumerate(points):
            rate = arr_cents // DAILY_REVENUE_DAYS
            if index + 1 < len(points):
                segment_end = points[index + 1][0] - timedelta(days=1)
            else:
                segment_end = END_DATE
            day = from_date
            while day <= segment_end:
                yield (account["id"], day.isoformat(), rate)
                day += timedelta(days=1)


def iter_usage_events(rng, accounts):
    """Yield (account_id, event_date, metric, units) seeded telemetry rows.

    Draw order: accounts by id, days ascending — one rng.random() check and,
    on a hit, one rng.randint() per account-day. Weekend days scale units
    down by an integer percent (WEEKEND_USAGE_PCT).
    """
    low, high = USAGE_UNITS_RANGE
    for account in accounts:
        account_id = account["id"]
        day = date.fromisoformat(account["created_at"])
        while day <= END_DATE:
            if rng.random() < USAGE_PROB:
                units = rng.randint(low, high)
                if day.weekday() >= 5:  # 5=Sat, 6=Sun
                    units = units * WEEKEND_USAGE_PCT // 100
                yield (account_id, day.isoformat(), USAGE_METRIC, units)
            day += timedelta(days=1)


def _insert_batched(conn, sql, rows, batch_size=INSERT_BATCH_ROWS):
    """Stream a row iterator into `sql` in bounded executemany chunks."""
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)


def _print_summary(db_path):
    """Print the facts the issue's acceptance checks eyeball without SQL."""
    conn = db.connect(db_path, readonly=True)
    try:
        print(f"Generated {db_path} (seed {SEED})")
        for table in TABLE_NAMES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count:,} rows")
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM revenue_events"
        ).fetchone()[0]
        print(f"  total revenue: {total_revenue:,} cents")
        q3 = dict(conn.execute(
            "SELECT substr(event_date, 1, 7), SUM(arr_delta_cents) "
            "FROM subscription_events "
            "WHERE event_date BETWEEN '2026-07-01' AND '2026-09-30' "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall())
        for month in ("2026-07", "2026-08", "2026-09"):
            print(f"  net-new ARR {month}: {q3.get(month, 0):,} cents")
        for table in EVENT_TABLE_NAMES:
            min_date, max_date = conn.execute(
                f"SELECT MIN(event_date), MAX(event_date) FROM {table}"
            ).fetchone()
            print(f"  {table} dates: {min_date} .. {max_date}")
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Generate the deterministic seeded mock SQLite database "
            "(data/app.db by default) with a Q3-2026 growth shape."
        )
    )
    parser.add_argument(
        "--db-path",
        default=str(db.DEFAULT_DB_PATH),
        help="target SQLite file, created fresh (default: repo-root data/app.db)",
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db_path)
    rng = random.Random(SEED)
    plan = plan_months(rng)
    accounts, subscription_events = build_accounts(rng, plan)
    timelines = _account_timelines(accounts, subscription_events)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Unlink first so reruns over an existing path are idempotent instead of
    # appending duplicate rows onto the old schema.
    if db_path.exists():
        db_path.unlink()
    conn = db.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.executemany(
            "INSERT INTO accounts (id, name, segment, plan, arr_cents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                (a["id"], a["name"], a["segment"], a["plan"], a["arr_cents"], a["created_at"])
                for a in accounts
            ),
        )
        conn.executemany(
            "INSERT INTO subscription_events "
            "(id, account_id, event_date, event_type, arr_delta_cents) "
            "VALUES (?, ?, ?, ?, ?)",
            subscription_events,
        )
        _insert_batched(
            conn,
            "INSERT INTO revenue_events (account_id, event_date, amount_cents) "
            "VALUES (?, ?, ?)",
            iter_revenue_events(accounts, timelines),
        )
        _insert_batched(
            conn,
            "INSERT INTO usage_events (account_id, event_date, metric, units) "
            "VALUES (?, ?, ?, ?)",
            iter_usage_events(rng, accounts),
        )
        conn.commit()
    finally:
        conn.close()
    _print_summary(db_path)
    return db_path


if __name__ == "__main__":
    main()
