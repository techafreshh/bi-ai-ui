# Sample CSVs (issue #72)

Tiny, deterministic reviewer fixtures that reproduce the seeded database's
golden shapes without committing the multi-MB `data/app.db`. Total payload
is ~18 KB (hard cap: 1 MB).

## Provenance

These files are **generated, not dumped**: `agent/scripts/export_samples.py`
builds a hand-pinned miniature dataset in code (fixed row literals plus a
seeded RNG for company names and baseline usage units) whose shapes mirror
the seeded profile of `agent/scripts/generate_data.py` — same schema
column order, same money-as-cents / dates-as-TEXT conventions, same
repriced-churn and Startup ingest-bug semantics at small scale. No row comes
from `data/app.db`, and no SQLite file is committed here (or anywhere:
`data/` stays gitignored).

## Files

| file | rows | shape inventory |
|---|---|---|
| `accounts.csv` | 30 | 20 Growth (the churn cohort) + 2 Growth, 5 Startup, 3 Enterprise; plans Starter/Growth/Scale all represented |
| `subscription_events.csv` | 71 | 30 `new` + 21 `upgrade` + **20 `cancel`** rows: one repriced-churn cluster — 20 Growth accounts created 2026-01-06..2026-01-25, each upgraded Growth→Scale then cancelled (zero-delta marker) inside the single window **2026-08-11..2026-08-28** (tenures ≥208 days) |
| `revenue_events.csv` | 584 | daily pro-rata slices (`arr_cents // 365` at the point-in-time ARR) for **2026-08-01..2026-08-31**; churned accounts stop inclusive on their cancel date, the rest run to 2026-08-31 |
| `usage_events.csv` | 42 | one Startup-only 7-day bug week **2026-09-08..2026-09-14** (35 baseline `api_calls` rows) containing one exact-duplicate `(account_id,event_date,metric,units)` pair (account 23, 2026-09-08) and one `units=NULL` row (empty field; account 25, 2026-09-12), plus 6 corruption-free confinement rows outside the week/segment |
| `sha256sums.txt` | — | SHA-256 of the four CSVs; paths are repo-root-relative so verification runs from the repo root |

`accounts.arr_cents == SUM(arr_delta_cents)` holds per account, mirroring
the generator's bookkeeping invariant.

## Regenerate

```sh
python agent/scripts/export_samples.py [--out-dir samples]
```

Reruns are byte-identical (fixed literals + fixed seed `20261020`).

## Verify

```sh
head -1 samples/accounts.csv  # id,name,segment,plan,arr_cents,created_at
du -cb samples/*.csv          # total must stay under 1048576 bytes
sha256sum -c samples/sha256sums.txt
```
