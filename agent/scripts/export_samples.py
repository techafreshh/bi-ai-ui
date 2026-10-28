"""Deterministic miniature sample-CSV exporter (issue #72).

Builds a hand-pinned tiny dataset that mirrors the seeded generator's
golden shapes at reviewer scale (~30 accounts, far under 1 MB total) and
writes it as schema-exact CSVs plus a `sha256sum -c`-compatible checksums
file under `samples/`:

    samples/accounts.csv, subscription_events.csv, revenue_events.csv,
    usage_events.csv, sha256sums.txt

The shapes reproduce `agent/scripts/generate_data.py`'s seeded profile by
construction (fixed row literals, never a dump of `data/app.db`):

  * repriced churn cluster: 20 Growth-segment accounts created on or before
    2026-01-31 (mirroring CHURN_CANDIDATE_CREATED_THROUGH) each carry a
    Growth->Scale "new plan" upgrade and a zero-delta 'cancel' marker
    inside a single August-2026 window (mirroring CHURN_WINDOW_START's
    pricing-change day), so every tenure exceeds six months;
  * Startup ingest-bug week: a single 7-day window (2026-09-08..2026-09-14,
    mirroring USAGE_BUG_WINDOW's start) holding Startup-segment usage rows
    only, with one exact-duplicate row and one units=NULL row;
  * daily pro-rata revenue (arr_cents // 365, mirroring DAILY_REVENUE_DAYS)
    priced at the point-in-time ARR, stopping inclusive on each churned
    account's cancel date.

Only names and baseline usage units flow through a seeded RNG (fixed
EXPORT_SEED, mirroring the generator's fixed SEED idiom); every
shape-critical field is a literal, so the >=20-cancels / duplicate-key /
NULL-units minimums hold without sampling luck. Run as:

    python agent/scripts/export_samples.py [--out-dir samples]
"""

import argparse
import csv
import hashlib
import random
from datetime import date, timedelta
from pathlib import Path

# Repo-root resolution from this file's location, never from the cwd
# (scripts/reset_data.sh precedent: ROOT derives from the script path).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "samples"

# Fixed seed for the cosmetic draws only (company names, baseline usage
# units) — the generator's generate_data.SEED value, so the provenance is
# obvious; shape-critical fields below are literals, never draws.
EXPORT_SEED = 20261020

# CSV headers, verbatim from generate_data.SCHEMA_SQL column order (the same
# tuples test_generate_data.EXPECTED_COLUMNS pins — no extra columns).
HEADERS = {
    "accounts": ("id", "name", "segment", "plan", "arr_cents", "created_at"),
    "subscription_events": (
        "id", "account_id", "event_date", "event_type", "arr_delta_cents",
    ),
    "revenue_events": ("id", "account_id", "event_date", "amount_cents"),
    "usage_events": ("id", "account_id", "event_date", "metric", "units"),
}
CSV_FILES = tuple(HEADERS)

# Money ladder, verbatim values from generate_data.PLANS / UPGRADE_DELTA_CENTS.
ARR_CENTS = {"Starter": 180_000, "Growth": 600_000, "Scale": 2_400_000}
NEXT_PLAN = {"Starter": "Growth", "Growth": "Scale"}
UPGRADE_DELTA_CENTS = {"Starter": 420_000, "Growth": 1_800_000}

# Churn cluster (issue #4 semantics at miniature scale): 20 Growth accounts
# (CHURN_IDS) created on/before CHURN_CANDIDATE_CUTOFF, each upgraded one
# rung then cancelled inside CHURN_MONTH — one repriced-churn cluster.
CHURN_IDS = tuple(range(1, 21))
CHURN_CANDIDATE_CUTOFF = "2026-01-31"
CHURN_MONTH = "2026-08"
CHURN_UPGRADE_DAY = date(2026, 8, 11)  # the pricing-change day, cf. 2026-08-10

# Ingest-bug week: Startup-segment usage only, 7 days from the seeded bug
# window's start (USAGE_BUG_WINDOW opens 2026-09-08).
BUG_WEEK_START = date(2026, 9, 8)
BUG_WEEK_DAYS = 7
BUG_SEGMENT = "Startup"
BUG_METRIC = "api_calls"  # cf. generate_data.USAGE_METRIC
USAGE_UNITS_RANGE = (120, 50_000)

# Revenue slice: daily pro-rata rows for August 2026 (churned accounts stop
# inclusive on their cancel date, mirroring iter_revenue_events).
REVENUE_START = date(2026, 8, 1)
REVENUE_END = date(2026, 8, 31)
PRO_RATA_DAYS = 365  # cf. generate_data.DAILY_REVENUE_DAYS

NAME_PREFIXES = ("Azure", "Borealis", "Cinder", "Dune", "Ember", "Fjord")
NAME_SUFFIXES = ("Dynamics", "Labs", "Systems", "Works", "Analytics", "Logic")

# Hand-pinned account specs: (segment, initial plan, created_at) per id.
# Ids 1..20 are the churn cluster; 21..30 cover every segment/plan without
# churning (21 carries a non-churn upgrade for variety).
ACCOUNT_SPECS = {
    **{
        account_id: ("Growth", "Growth", f"2026-01-{6 + (account_id - 1):02d}")
        for account_id in CHURN_IDS
    },
    21: ("Growth", "Growth", "2026-03-10"),
    22: ("Growth", "Starter", "2026-04-14"),
    23: ("Startup", "Starter", "2026-08-25"),
    24: ("Startup", "Starter", "2026-08-26"),
    25: ("Startup", "Starter", "2026-08-27"),
    26: ("Startup", "Starter", "2026-08-28"),
    27: ("Startup", "Starter", "2026-08-29"),
    28: ("Enterprise", "Scale", "2026-02-15"),
    29: ("Enterprise", "Growth", "2026-05-20"),
    30: ("Enterprise", "Starter", "2026-06-11"),
}


def _unique_name(rng, used_names):
    """Draw a combo-word company name until unused (membership only — the
    set is never iterated, so no hash-order dependence)."""
    while True:
        name = (
            f"{rng.choice(NAME_PREFIXES)} {rng.choice(NAME_SUFFIXES)} "
            f"{rng.randint(1, 999):03d}"
        )
        if name not in used_names:
            used_names.add(name)
            return name


def build_dataset(rng):
    """Return (accounts, subscription_events, revenue_events, usage_events)
    row lists (dicts, id-less — ids are assigned in emit order at write
    time). Account plan/arr_cents are derived from the account's own
    subscription events, so accounts.arr_cents == SUM(arr_delta_cents)
    holds verbatim, mirroring the generator's bookkeeping invariant."""
    used_names = set()
    accounts = []
    sub_events = []  # (account_id, event_date, event_type, arr_delta_cents)
    for account_id in sorted(ACCOUNT_SPECS):
        segment, initial_plan, created_at = ACCOUNT_SPECS[account_id]
        accounts.append({
            "id": account_id,
            "name": _unique_name(rng, used_names),
            "segment": segment,
            "plan": initial_plan,
            "arr_cents": ARR_CENTS[initial_plan],
            "created_at": created_at,
        })
        sub_events.append(
            (account_id, created_at, "new", ARR_CENTS[initial_plan])
        )

    churn_dates = {}
    for index, account_id in enumerate(CHURN_IDS):
        # Upgrade on/after the pricing-change day, cancel 3..6 days later:
        # every pair lands inside the single CHURN_MONTH window.
        upgrade_day = CHURN_UPGRADE_DAY + timedelta(days=index % 12)
        cancel_day = upgrade_day + timedelta(days=3 + index % 4)
        account = accounts[account_id - 1]
        old_plan = account["plan"]
        sub_events.append((
            account_id, upgrade_day.isoformat(), "upgrade",
            UPGRADE_DELTA_CENTS[old_plan],
        ))
        sub_events.append((account_id, cancel_day.isoformat(), "cancel", 0))
        account["plan"] = NEXT_PLAN[old_plan]
        account["arr_cents"] += UPGRADE_DELTA_CENTS[old_plan]
        churn_dates[account_id] = cancel_day

    # One non-churn upgrade (account 21, before the revenue slice, so its
    # August rate is flat at the post-upgrade ARR).
    account = accounts[21 - 1]
    sub_events.append((21, "2026-07-15", "upgrade", UPGRADE_DELTA_CENTS["Growth"]))
    account["plan"] = NEXT_PLAN["Growth"]
    account["arr_cents"] += UPGRADE_DELTA_CENTS["Growth"]

    # Chronological emission, generator idiom: sort by (date, account) so
    # ids run in date order.
    sub_events.sort(key=lambda event: (event[1], event[0]))
    subscription_events = [
        {"id": index, "account_id": account_id, "event_date": event_date,
         "event_type": event_type, "arr_delta_cents": delta}
        for index, (account_id, event_date, event_type, delta)
        in enumerate(sub_events, 1)
    ]

    # Daily pro-rata revenue at the point-in-time ARR, stopping inclusive on
    # each churned account's cancel date.
    timelines = {}  # account_id -> sorted [(from_date, arr_cents)]
    for account in accounts:
        running = 0
        points = []
        for _account_id, event_date, _type, delta in sorted(
            (event for event in sub_events if event[0] == account["id"]),
            key=lambda event: (event[1], event[3]),
        ):
            running += delta
            points.append((date.fromisoformat(event_date), running))
        timelines[account["id"]] = points
    revenue_rows = []
    for account in accounts:
        account_id = account["id"]
        end = min(churn_dates.get(account_id, REVENUE_END), REVENUE_END)
        for from_day, arr_cents in timelines[account_id]:
            rate = arr_cents // PRO_RATA_DAYS
            segment_end = end
            day = max(from_day, REVENUE_START)
            # Skip pre-slice points (the 'new' events predate August) except
            # the point active when the slice opens.
            if day > segment_end:
                continue
            while day <= segment_end:
                revenue_rows.append((account_id, day.isoformat(), rate))
                day += timedelta(days=1)
    revenue_rows.sort()
    revenue_events = [
        {"id": index, "account_id": account_id, "event_date": event_date,
         "amount_cents": amount}
        for index, (account_id, event_date, amount)
        in enumerate(revenue_rows, 1)
    ]

    # Startup-only bug-week baseline: one row per account-day, seeded units.
    usage_rows = []  # (account_id, event_date, metric, units|None)
    startup_ids = [
        account["id"] for account in accounts
        if account["segment"] == BUG_SEGMENT
    ]
    baseline = {}
    for account_id in startup_ids:
        for offset in range(BUG_WEEK_DAYS):
            day = BUG_WEEK_START + timedelta(days=offset)
            units = rng.randint(*USAGE_UNITS_RANGE)
            baseline[(account_id, day.isoformat())] = units
            usage_rows.append(
                (account_id, day.isoformat(), BUG_METRIC, units)
            )
    # The planted pair: an exact duplicate of one baseline row…
    dup_key = (startup_ids[0], BUG_WEEK_START.isoformat())
    usage_rows.append((dup_key[0], dup_key[1], BUG_METRIC, baseline[dup_key]))
    # …and a NULL-units row on a different key (empty field in the CSV).
    null_key = (startup_ids[2], (BUG_WEEK_START + timedelta(days=4)).isoformat())
    usage_rows = [
        (account_id, event_date, metric, None)
        if (account_id, event_date) == null_key
        else (account_id, event_date, metric, units)
        for account_id, event_date, metric, units in usage_rows
    ]
    # Confinement rows outside the week/segment: corruption-free by
    # construction, so the bug reads as Startup-week-only.
    for account_id, day in (
        (21, date(2026, 8, 5)), (21, date(2026, 8, 6)),
        (28, date(2026, 8, 5)), (29, date(2026, 8, 6)),
        (23, date(2026, 8, 30)), (30, date(2026, 8, 7)),
    ):
        usage_rows.append(
            (account_id, day.isoformat(), BUG_METRIC,
             rng.randint(*USAGE_UNITS_RANGE))
        )
    # Stable sort keeps the exact duplicate adjacent to its original.
    usage_rows.sort(key=lambda row: (row[0], row[1]))
    usage_events = [
        {"id": index, "account_id": account_id, "event_date": event_date,
         "metric": metric, "units": units}
        for index, (account_id, event_date, metric, units)
        in enumerate(usage_rows, 1)
    ]
    return accounts, subscription_events, revenue_events, usage_events


def write_samples(out_dir):
    """Build the dataset and write the four CSVs plus sha256sums.txt into
    out_dir. Returns {filename: (rows, bytes)} for the summary/README."""
    rng = random.Random(EXPORT_SEED)
    tables = dict(zip(
        CSV_FILES, build_dataset(rng), strict=True,
    ))
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    for filename in CSV_FILES:
        path = out_dir / f"{filename}.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(HEADERS[filename])
            for row in tables[filename]:
                writer.writerow([
                    "" if value is None else value
                    for value in (row[column] for column in HEADERS[filename])
                ])
        stats[filename] = (len(tables[filename]), path.stat().st_size)
    # Checksum lines carry the repo-root-relative path so `sha256sum -c
    # samples/sha256sums.txt` passes from the repo root (bare basenames
    # would resolve against the cwd and fail there).
    sums_path = out_dir / "sha256sums.txt"
    with open(sums_path, "w", newline="", encoding="utf-8") as handle:
        for filename in CSV_FILES:
            csv_path = out_dir / f"{filename}.csv"
            digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            try:
                rel = csv_path.resolve().relative_to(
                    REPO_ROOT.resolve()
                ).as_posix()
            except ValueError:
                # Out-of-tree --out-dir (e.g. a tmp_path probe): fall back
                # to the bare filename so the sums file stays self-contained.
                rel = csv_path.name
            handle.write(f"{digest}  {rel}\n")
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Export the deterministic miniature sample CSVs (samples/ by "
            "default): schema-exact reviewer fixtures reproducing the "
            "seeded generator's repriced-churn and ingest-bug shapes, plus "
            "a sha256sum -c-compatible checksums file."
        )
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="target directory for the CSVs + sha256sums.txt "
        "(default: repo-root samples/)",
    )
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    stats = write_samples(out_dir)
    total = sum(size for _rows, size in stats.values())
    print(f"Exported {out_dir} (seed {EXPORT_SEED})")
    for filename in CSV_FILES:
        rows, size = stats[filename]
        print(f"  {filename}.csv: {rows} rows, {size} bytes")
    print(f"  total: {total} bytes")
    return out_dir


if __name__ == "__main__":
    main()
