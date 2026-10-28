"""Deterministic seeded SQLite data generator (issues #3 and #4).

Running `python agent/scripts/generate_data.py` creates `data/app.db`
(gitignored) with mock B2B-SaaS data covering the 24 months ending
2026-09-30: ~5–8k accounts plus daily subscription / revenue / usage event
tables, all reproducible from one fixed seed — running the script twice with
the same flags yields identical per-table row counts and identical total
revenue to the cent.

Two profiles select WHICH shapes are planted (`--profile`, `--seed`):

  * `--profile seeded` (default): the three choreographed demo shapes
    below, always from the fixed module SEED — --seed is accepted but
    IGNORED here, so the default output is byte-identical to the PRD's demo
    contract no matter what it is set to (the README script, the golden
    tests and verify_demo.py all pin it, so the default output must never
    drift).
  * `--profile random`: a same-period dataset WITHOUT choreographed shapes
    — organic new-account counts, a randomly placed repricing churn spike
    (Jun 1–Sep 16 2026, any segment, 15–45 accounts), a light organic churn
    tail (10–30 cancels scattered before June 2026 so the baseline is
    nonzero), and a randomly placed ingest bug (14 days inside Jul–Sep 2026,
    any segment, ~12–20% duplicate + ~5–8% null shares). `--seed` picks the
    shapes; the same seed always rebuilds the identical dataset. This
    profile is the test harness for "does the agent compose UI from data it
    was never staged for?" — verify_demo.py's planted-shape probes do NOT
    apply to it.

Seeded profile's three shapes (the default, issues #3 and #4):

  1. Q3 growth ramp (issue #3): an Aug-1-2026 go-to-market campaign is
     baked in as pinned monthly new-account counts, producing strong
     net-new ARR growth through Q3 2026.
  2. Churn spike (issue #4): following a 2026-08-10 pricing change, exactly
     40 Growth-segment accounts adopt a new plan (one ladder rung up) and
     carry a zero-delta 'cancel' marker between 2026-08-10 and 2026-09-30.
     Candidates are created on or before 2026-01-31, so every churned
     account's tenure exceeds six months by construction. Revenue and
     usage stop (inclusive) on the cancel date; the accounts row keeps its
     churn-time plan/ARR, so accounts.arr_cents == SUM(arr_delta_cents)
     still holds verbatim.
  3. Ingest bug (issue #4): usage rows for Startup-segment accounts only,
     inside 2026-09-08..2026-09-21, are corrupted per emitted row by one
     draw — 15% also emit an exact duplicate (same account, ts, metric,
     amount) and 6% emit with units = NULL. NULL units exist only in these
     planted rows; other segments see zero duplicates and zero NULLs.

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
        event_type TEXT NOT NULL,      -- 'new' | 'upgrade' | 'cancel' ('cancel' = churn marker, issue #4)
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
        units INTEGER);                -- NULL only in the planted ingest-bug rows (issue #4)

Net-new ARR for a month := SUM(arr_delta_cents) over that month's
subscription_events. The 2024-10 founding cohort is pinned to 2024-10-01,
which pins the minimum event date inside the acceptance window
2024-10-01..2024-10-31; every account bills daily pro-rata through
2026-09-30 (churned accounts stop on their cancel date), so that exact day
is always the maximum event date.

Growth shape: baseline months draw 210..270 new accounts; the campaign
months pin 2026-07: 280, 2026-08: 560, 2026-09: 840 (2× and 3× July). With
the weighted plan mix (E[ARR] ≈ $7,710 per new account), Jul/Aug/Sep net-new
ARR land near $2.2M / $4.3M / $6.5M — each month ≥ the prior one and
Aug+Sep ≥ 1.5× Jul with sampling noise roughly an order of magnitude below
the ramp steps. Issue #4's churn repricing upgrades add at most ≈$0.7M
gross across Aug–Sep 2026 (40 accounts × ≤$18k, ≈2/3 landing in August),
shifting the ≈$1.9M Sep−Aug step margin by at most ≈$0.23M — the Q3
monotonicity contract keeps holding by wide margin.

Determinism is structural, not hoped for: all randomness flows through ONE
`random.Random(seed)` (seed from --seed, default the fixed SEED) passed to
pure builders that consume draws in a fixed order (monthly counts →
per-account plan/segment/day/name → monthly upgrade picks → churn cohort
picks and cancel/upgrade dates → usage draws, with ingest-bug rolls
interleaved per in-window segment row). The random profile draws its shape
parameters first (spike start/segment/count, organic-cancel count, bug
window/segment/shares) and then runs the same chain; the profile changes
the shape parameters, never the draw order. New draws join only at the end
of that chain, never above existing ones. No module-global `random`, no
iteration over sets (PYTHONHASHSEED independence), sequential ids,
integer-only money math.
"""

import argparse
import calendar
import random
import sys
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

# Churn spike (issue #4): exactly CHURNED_ACCOUNT_COUNT accounts cancel in
# the window — 40 pins inside the acceptance band 38–45 and reproduces
# identically on rerun; all are Growth-segment (acceptance: ≥80%) and
# created through CHURN_CANDIDATE_CREATED_THROUGH, so the smallest possible
# tenure (earliest cancel 2026-08-10 minus 2026-01-31) is 191 days and the
# "median tenure > 6 months" clause holds by construction.
CHURNED_ACCOUNT_COUNT = 40
CHURN_WINDOW_START = date(2026, 8, 10)   # the pricing-change day
CHURN_WINDOW_END = date(2026, 9, 30)
CHURN_CANDIDATE_CREATED_THROUGH = "2026-01-31"

# Ingest bug (issue #4): per emitted usage row whose account is Startup
# (the only segment affected) and whose date is in the 14-day window, one
# roll decides — 15% yield an exact duplicate copy, 6% yield units = NULL.
# Against total window rows the planted shares are 15/115 ≈ 13% duplicated
# and 6/115 ≈ 5.2% null (against baseline rows 15% and 6%) — ≥2.6× above
# the acceptance floors (≥5% / >2%) under either denominator reading;
# other segments never see a roll, so their rates are exactly 0 (<0.5%
# caps).
USAGE_BUG_WINDOW = ("2026-09-08", "2026-09-21")
USAGE_BUG_SEGMENT = "Startup"
USAGE_BUG_DUPLICATE_SHARE = 0.15
USAGE_BUG_NULL_SHARE = 0.06

# Random profile (--profile random): the same period, organic shapes. The
# churn spike may start any day in RANDOM_SPIKE_EARLIEST..RANDOM_SPIKE_
# LATEST_START (so the window always spans ≥14 days to data end) with any
# segment and RANDOM_SPIKE_COUNT_RANGE accounts; its repricing upgrade
# precedes each cancel by ≤ RANDOM_UPGRADE_BACK_DAYS (a realistic
# repriced-then-cancelled signature, and the same one _churn_cause reads).
# The light organic churn tail (RANDOM_ORGANIC_CANCEL_COUNT cancels before
# RANDOM_ORGANIC_LAST_DAY, no paired upgrade) keeps the churn baseline
# nonzero so the variance callout measures against a real baseline. The
# bug window spans RANDOM_BUG_DAYS ending by data end, inside Jul 2026 or
# later so quality.recent_problem_scope's trailing-120 lookback finds it;
# shares mirror the seeded bug's corruption bands.
RANDOM_NEW_COUNT_RANGE = (180, 320)
RANDOM_SPIKE_EARLIEST = date(2026, 6, 1)
RANDOM_SPIKE_LATEST_START = date(2026, 9, 16)
RANDOM_SPIKE_COUNT_RANGE = (15, 45)
RANDOM_UPGRADE_BACK_DAYS = 14
RANDOM_ORGANIC_CANCEL_COUNT = (10, 30)
RANDOM_ORGANIC_LAST_DAY = date(2026, 5, 31)
RANDOM_ORGANIC_MIN_AGE_DAYS = 30
RANDOM_BUG_DAYS = 14
RANDOM_BUG_EARLIEST = date(2026, 7, 1)
RANDOM_BUG_LATEST_START = date(2026, 9, 17)
RANDOM_DUP_SHARE_RANGE = (0.12, 0.20)
RANDOM_NULL_SHARE_RANGE = (0.05, 0.08)

PROFILES = ("seeded", "random")

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
    units INTEGER
);
CREATE INDEX idx_subscription_events_event_date ON subscription_events(event_date);
CREATE INDEX idx_revenue_events_event_date ON revenue_events(event_date);
CREATE INDEX idx_usage_events_event_date ON usage_events(event_date);
"""


def plan_months(rng, *, campaign_counts=CAMPAIGN_MONTH_COUNTS,
                baseline_range=BASELINE_NEW_COUNT_RANGE):
    """Return [(month_key, new_count)] for the 24 covered months.

    Campaign months take pinned counts (no draw) when campaign_counts is
    given — the seeded profile's CAMPAIGN_MONTH_COUNTS; the random profile
    passes None, so every month draws inside baseline_range in
    chronological order. The defaults reproduce the planted profile's draw
    sequence exactly.
    """
    plan = []
    year, month = FIRST_YEAR, FIRST_MONTH_NUM
    for _ in range(MONTHS_COVERED):
        key = f"{year:04d}-{month:02d}"
        pinned = campaign_counts.get(key) if campaign_counts else None
        count = pinned if pinned is not None else rng.randint(*baseline_range)
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


def apply_churn(rng, accounts, subscription_events, *, segment="Growth",
                window_start=CHURN_WINDOW_START, window_end=CHURN_WINDOW_END,
                count=CHURNED_ACCOUNT_COUNT,
                candidate_cutoff=CHURN_CANDIDATE_CREATED_THROUGH,
                upgrade_back_days=None):
    """Plant a repricing churn spike (issue #4's seeded shape; the keyword
    defaults reproduce it exactly, the random profile overrides them).

    Candidates: `segment`-segment accounts created on or before
    candidate_cutoff that still have a ladder rung above (`plan in
    UPGRADE_DELTA_CENTS`), in id order — rng.sample over the list picks
    exactly count (no set iteration anywhere). Per pick: draw the cancel
    date uniformly in [window_start, window_end], then the paired "new
    plan" upgrade uniformly in [window_start, cancel] — or, when
    upgrade_back_days is given (the random profile's realistic
    repriced-days-before-cancelling signature), uniformly in
    [cancel - upgrade_back_days, cancel]. The upgrade event moves the
    account one rung up; the 'cancel' event carries arr_delta_cents = 0
    and is purely the churn marker — the account row keeps its churn-time
    plan/ARR, so accounts.arr_cents == SUM(arr_delta_cents) keeps holding
    verbatim.

    Returns {account_id: cancel_date}; revenue and usage emission for these
    accounts stops (inclusive) on the cancel date.
    """
    candidates = [
        account for account in sorted(accounts, key=lambda item: item["id"])
        if account["segment"] == segment
        and account["plan"] in UPGRADE_DELTA_CENTS  # a next rung must exist
        and account["created_at"] <= candidate_cutoff
    ]
    picked = rng.sample(candidates, count)
    churn_dates = {}
    next_event_id = len(subscription_events) + 1
    for account in picked:
        cancel_date = window_start + timedelta(
            days=rng.randint(0, (window_end - window_start).days)
        )
        back = (
            upgrade_back_days
            if upgrade_back_days is not None
            else (cancel_date - window_start).days
        )
        # The upgrade window is anchored so the drawn offset lands in
        # [cancel - back, cancel]: the repriced-then-cancelled signature
        # (≤ upgrade_back_days before cancelling) for the random profile —
        # and, because the seeded profile's back spans the whole window, the
        # historical [window_start, cancel] range there, keeping its output
        # byte-identical. Clamped up to the account's creation day so the
        # upgrade never precedes the account's own 'new' event (the random
        # profile's candidates may be created just before the window).
        upgrade_date = (cancel_date - timedelta(days=back)) + timedelta(
            days=rng.randint(0, back)
        )
        created_day = date.fromisoformat(account["created_at"])
        if upgrade_date < created_day:
            upgrade_date = created_day
        old_plan = account["plan"]
        subscription_events.append((
            next_event_id, account["id"], upgrade_date.isoformat(),
            "upgrade", UPGRADE_DELTA_CENTS[old_plan],
        ))
        subscription_events.append((
            next_event_id + 1, account["id"], cancel_date.isoformat(),
            "cancel", 0,
        ))
        next_event_id += 2
        account["plan"] = NEXT_PLAN[old_plan]
        account["arr_cents"] += UPGRADE_DELTA_CENTS[old_plan]
        churn_dates[account["id"]] = cancel_date
    return churn_dates


def apply_organic_churn(rng, accounts, subscription_events, exclude_ids,
                        total, last_day=RANDOM_ORGANIC_LAST_DAY):
    """Scatter a light organic churn tail (random profile only).

    `total` cancels (no paired upgrade — they must never read as the spike's
    repricing signature) drawn uniformly among accounts created early enough
    to age RANDOM_ORGANIC_MIN_AGE_DAYS past creation before `last_day`.
    Cancel dates land between that age floor and `last_day`, which keeps
    them strictly before the spike's earliest possible start (June 2026), so
    the baseline months carry a real but unspectacular churn rate and the
    spike month stands out against it. Account ids in `exclude_ids` (the
    spike cohort) are skipped; retry-bounded like _unique_name so the draw
    count stays bounded.

    Returns {account_id: cancel_date} merged into the same churn_dates map
    the spike produced — revenue and usage stop on the cancel date alike.
    """
    first_day = date(FIRST_YEAR, FIRST_MONTH_NUM, 1)
    earliest = first_day + timedelta(days=RANDOM_ORGANIC_MIN_AGE_DAYS)
    picked_ids = set(exclude_ids)
    churn_dates = {}
    next_event_id = len(subscription_events) + 1
    attempts = 0
    attempts_cap = NAME_ATTEMPTS * max(1, total)
    while len(churn_dates) < total and attempts < attempts_cap:
        attempts += 1
        account = accounts[rng.randrange(len(accounts))]
        if account["id"] in picked_ids:
            continue
        created = date.fromisoformat(account["created_at"])
        floor = max(created + timedelta(days=RANDOM_ORGANIC_MIN_AGE_DAYS), earliest)
        if floor > last_day:
            continue
        cancel_date = floor + timedelta(days=rng.randint(0, (last_day - floor).days))
        picked_ids.add(account["id"])
        subscription_events.append((
            next_event_id, account["id"], cancel_date.isoformat(), "cancel", 0,
        ))
        next_event_id += 1
        churn_dates[account["id"]] = cancel_date
    return churn_dates


def _sort_and_renumber_events(subscription_events):
    """Restore global event chronology after the churn append (issue #4).

    Churn events are appended after every earlier id exists, so id order
    briefly stops matching date order. Stable sort by (event_date, id) and
    reassignment of ids 1..N keeps the documented subscription_events
    contract — chronological rows, chronological ids — which is exactly
    what `_account_timelines`' tuple sort relies on.
    """
    subscription_events.sort(key=lambda event: (event[2], event[0]))
    subscription_events[:] = [
        (index, account_id, event_date, event_type, delta)
        for index, (_old_id, account_id, event_date, event_type, delta)
        in enumerate(subscription_events, 1)
    ]


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


def iter_revenue_events(accounts, timelines, churn_dates):
    """Yield (account_id, event_date, amount_cents) for every active day.

    One pro-rata row per account-day (|created_at, END_DATE|) priced at the
    point-in-time ARR // DAILY_REVENUE_DAYS (floor, integers only) — daily
    event tables per the issue, and every date in the window gets rows so
    the min/max acceptance dates hold by construction. Churned accounts
    (issue #4) stop inclusive on their cancel date — the churn day is the
    last billed day.
    """
    for account in accounts:
        points = timelines[account["id"] - 1]
        churn_end = churn_dates.get(account["id"], END_DATE)
        for index, (from_date, arr_cents) in enumerate(points):
            rate = arr_cents // DAILY_REVENUE_DAYS
            if index + 1 < len(points):
                segment_end = points[index + 1][0] - timedelta(days=1)
            else:
                segment_end = END_DATE
            if segment_end > churn_end:
                segment_end = churn_end
            day = from_date
            while day <= segment_end:
                yield (account["id"], day.isoformat(), rate)
                day += timedelta(days=1)


def iter_usage_events(rng, accounts, churn_dates):
    """Yield (account_id, event_date, metric, units) seeded telemetry rows.

    Draw order: accounts by id, days ascending — one rng.random() check and,
    on a hit, one rng.randint() per account-day. Weekend days scale units
    down by an integer percent (WEEKEND_USAGE_PCT). Churned accounts
    (issue #4) stop inclusive on their cancel date.
    """
    low, high = USAGE_UNITS_RANGE
    for account in accounts:
        account_id = account["id"]
        day = date.fromisoformat(account["created_at"])
        end = churn_dates.get(account_id, END_DATE)
        while day <= end:
            if rng.random() < USAGE_PROB:
                units = rng.randint(low, high)
                if day.weekday() >= 5:  # 5=Sat, 6=Sun
                    units = units * WEEKEND_USAGE_PCT // 100
                yield (account_id, day.isoformat(), USAGE_METRIC, units)
            day += timedelta(days=1)


def emit_usage_events(rng, accounts, churn_dates, *, window=USAGE_BUG_WINDOW,
                      segment=USAGE_BUG_SEGMENT,
                      duplicate_share=USAGE_BUG_DUPLICATE_SHARE,
                      null_share=USAGE_BUG_NULL_SHARE):
    """Wrap `iter_usage_events` with an ingest bug (issue #4's seeded
    shape; the keyword defaults reproduce it exactly, the random profile
    overrides them).

    Streaming pass-through: memory stays O(1), ids stay insert-ordered and
    each baseline row flows through untouched except rows whose account is
    `segment`-segment and whose event_date is in `window` — exactly one
    rng.random() roll per such row decides: below `duplicate_share` the row
    is also emitted as an exact copy (same account, ts, metric, amount —
    the duplicate group size is always exactly 2, since the baseline emits
    at most one row per account-day); below that plus `null_share` the row
    is emitted with units = NULL. Other segments never see a roll, so their
    duplicate and null rates are exactly 0.
    """
    accounts_by_id = {account["id"]: account for account in accounts}
    window_start, window_end = window
    for row in iter_usage_events(rng, accounts, churn_dates):
        account_id, event_date, metric, units = row
        if (event_date < window_start or event_date > window_end
                or accounts_by_id[account_id]["segment"] != segment):
            yield row
            continue
        roll = rng.random()
        if roll < duplicate_share:
            yield row
            yield row  # exact duplicate: same account, ts, metric, amount
        elif roll < duplicate_share + null_share:
            yield (account_id, event_date, metric, None)
        else:
            yield row


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


def _draw_random_shapes(rng):
    """Draw the random profile's shape parameters (one fixed draw order).

    Returns a dict with every shape the random profile's builders override:
    spike window (any day in the Jun 1–Sep 16 2026 start range), spike
    segment and size (15–45), organic-cancel count (10–30), bug window
    (14 days starting Jul 1 2026 or later, ending by data end) and the
    bug's duplicate/null shares. Shapes are drawn BEFORE the builders so
    the builder draw order stays the seeded profile's — the profile changes
    the shape parameters, never the chain.
    """
    spike_days = (RANDOM_SPIKE_LATEST_START - RANDOM_SPIKE_EARLIEST).days
    spike_start = RANDOM_SPIKE_EARLIEST + timedelta(days=rng.randint(0, spike_days))
    spike_end = END_DATE
    segment_names = tuple(name for name, _share in SEGMENT_MIX)
    organic_total = rng.randint(*RANDOM_ORGANIC_CANCEL_COUNT)
    bug_start = RANDOM_BUG_EARLIEST + timedelta(
        days=rng.randint(0, (RANDOM_BUG_LATEST_START - RANDOM_BUG_EARLIEST).days)
    )
    bug_end = bug_start + timedelta(days=RANDOM_BUG_DAYS - 1)
    return {
        "spike": {
            "start": spike_start,
            "end": spike_end,
            "segment": rng.choice(segment_names),
            "count": rng.randint(*RANDOM_SPIKE_COUNT_RANGE),
            "upgrade_back_days": RANDOM_UPGRADE_BACK_DAYS,
        },
        "organic_cancels": organic_total,
        "bug": {
            "start": bug_start,
            "end": bug_end,
            "segment": rng.choice(segment_names),
            "duplicate_share": round(
                rng.uniform(*RANDOM_DUP_SHARE_RANGE), 4
            ),
            "null_share": round(rng.uniform(*RANDOM_NULL_SHARE_RANGE), 4),
        },
    }


def _print_summary(db_path, *, profile="seeded", seed=SEED, shapes=None):
    """Print the facts the issue's acceptance checks eyeball without SQL.

    The trailing churn/ingest-bug block prints the given shapes' windows —
    the seeded profile's pinned windows when shapes is None (backward-
    compatible output), the drawn random-profile shapes otherwise."""
    conn = db.connect(db_path, readonly=True)
    try:
        print(f"Generated {db_path} (seed {seed}, profile {profile})")
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
        shapes = shapes or {}
        spike = shapes.get("spike") or {}
        bug = shapes.get("bug") or {}
        window = (
            (spike.get("start") or CHURN_WINDOW_START).isoformat(),
            (spike.get("end") or CHURN_WINDOW_END).isoformat(),
        )
        churned = conn.execute(
            "SELECT COUNT(*) FROM subscription_events "
            "WHERE event_type = 'cancel' AND event_date BETWEEN ? AND ?",
            window,
        ).fetchone()[0]
        by_segment = conn.execute(
            "SELECT a.segment, COUNT(*) FROM subscription_events s "
            "JOIN accounts a ON a.id = s.account_id "
            "WHERE s.event_type = 'cancel' AND s.event_date BETWEEN ? AND ? "
            "GROUP BY 1 ORDER BY 1",
            window,
        ).fetchall()
        print(
            f"  churned accounts (cancels) {window[0]}..{window[1]}: "
            f"{churned:,} — "
            + ", ".join(f"{segment}={count:,}" for segment, count in by_segment)
        )
        bug_window = (
            (bug.get("start") or date.fromisoformat(USAGE_BUG_WINDOW[0])).isoformat(),
            (bug.get("end") or date.fromisoformat(USAGE_BUG_WINDOW[1])).isoformat(),
        )
        bug_segment = bug.get("segment") or USAGE_BUG_SEGMENT
        dup_rows = conn.execute(
            "SELECT COALESCE(SUM(n - 1), 0) FROM ("
            "  SELECT COUNT(*) AS n FROM usage_events u "
            "  JOIN accounts a ON a.id = u.account_id "
            "  WHERE u.event_date BETWEEN ? AND ? AND a.segment = ? "
            "  GROUP BY u.account_id, u.event_date, u.units HAVING n > 1"
            ")",
            (bug_window[0], bug_window[1], bug_segment),
        ).fetchone()[0]
        null_rows = conn.execute(
            "SELECT COUNT(*) FROM usage_events u "
            "JOIN accounts a ON a.id = u.account_id "
            "WHERE u.event_date BETWEEN ? AND ? AND a.segment = ? "
            "AND u.units IS NULL",
            (bug_window[0], bug_window[1], bug_segment),
        ).fetchone()[0]
        print(
            f"  ingest bug {bug_window[0]}..{bug_window[1]} "
            f"({bug_segment} only): duplicate rows {dup_rows:,}, "
            f"null-amount rows {null_rows:,}"
        )
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Generate the deterministic seeded mock SQLite database "
            "(data/app.db by default) with the Q3-2026 growth ramp, the "
            "Aug-2026 churn spike and the Sept-2026 ingest-bug shapes — "
            "or, with --profile random, a same-period dataset with organic "
            "growth and randomly placed, seed-picked shapes."
        )
    )
    parser.add_argument(
        "--db-path",
        default=str(db.DEFAULT_DB_PATH),
        help="target SQLite file, created fresh (default: repo-root data/app.db)",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="seeded",
        help=(
            "seeded (default): the PRD's three choreographed demo shapes; "
            "random: organic growth + randomly placed churn spike and "
            "ingest bug (shapes picked by --seed)"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED,
        help="RNG seed for the random profile's drawn shapes (default: the "
        "fixed demo seed); any value yields a reproducible dataset. The "
        "seeded profile ignores --seed — its output is pinned to the fixed "
        "demo seed.",
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db_path)
    # The seeded profile is the PRD's demo contract: its output is pinned to
    # the fixed module SEED regardless of --seed (the golden tests and
    # verify_demo.py pin it verbatim), so a --seed value only shapes the
    # random profile's drawn dataset.
    rng = random.Random(args.seed if args.profile == "random" else SEED)
    if args.profile == "random":
        shapes = _draw_random_shapes(rng)
        spike = shapes["spike"]
        bug = shapes["bug"]
        plan = plan_months(rng, campaign_counts=None, baseline_range=RANDOM_NEW_COUNT_RANGE)
        accounts, subscription_events = build_accounts(rng, plan)
        churn_dates = apply_churn(
            rng,
            accounts,
            subscription_events,
            segment=spike["segment"],
            window_start=spike["start"],
            window_end=spike["end"],
            count=spike["count"],
            candidate_cutoff=spike["start"].isoformat(),
            upgrade_back_days=spike["upgrade_back_days"],
        )
        churn_dates.update(apply_organic_churn(
            rng, accounts, subscription_events, churn_dates,
            shapes["organic_cancels"],
        ))
        _sort_and_renumber_events(subscription_events)
        timelines = _account_timelines(accounts, subscription_events)
        bug_window = (bug["start"].isoformat(), bug["end"].isoformat())
        usage_events = emit_usage_events(
            rng,
            accounts,
            churn_dates,
            window=bug_window,
            segment=bug["segment"],
            duplicate_share=bug["duplicate_share"],
            null_share=bug["null_share"],
        )
        summary_shapes = shapes
    else:
        plan = plan_months(rng)
        accounts, subscription_events = build_accounts(rng, plan)
        churn_dates = apply_churn(rng, accounts, subscription_events)
        _sort_and_renumber_events(subscription_events)
        timelines = _account_timelines(accounts, subscription_events)
        usage_events = emit_usage_events(rng, accounts, churn_dates)
        summary_shapes = None
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
            iter_revenue_events(accounts, timelines, churn_dates),
        )
        _insert_batched(
            conn,
            "INSERT INTO usage_events (account_id, event_date, metric, units) "
            "VALUES (?, ?, ?, ?)",
            usage_events,
        )
        conn.commit()
    finally:
        conn.close()
    _print_summary(
        db_path,
        profile=args.profile,
        seed=args.seed if args.profile == "random" else SEED,
        shapes=summary_shapes,
    )
    return db_path


if __name__ == "__main__":
    main()
