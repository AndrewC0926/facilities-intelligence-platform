# Facilities Intelligence Platform (FIP)

**One source of truth for a multi-site hardware company's facilities portfolio.**

Most facilities orgs answer questions like *"which sites have quality problems?"*
or *"which program is about to outgrow its building?"* by emailing three teams and
stitching spreadsheets together for a week. FIP collapses that into a single
queryable model: it ingests the exports those teams already produce
(ERP/CMMS, MRP, HRIS), reconciles them — including a messy newly-acquired site —
and publishes a layer of documented SQL views that a dashboard (or Tableau) reads
directly.

A facilities VP can open the dashboard and immediately answer:

- **Which sites have quality problems?** (and are they getting worse?)
- **Where are we over/under capacity?** (people with no desk vs. empty seats)
- **What does each site cost per square foot, all-in?**
- **Which programs are about to outgrow their building** — *and in which quarter?*

---

## Architecture (5 lines)

```
1. SOURCES   simulated exports → seeds/*.csv      (ERP/CMMS · MRP · HRIS · acquired-site dump)
2. ETL       fip/etl.py        → cleans & reconciles dirty data into the canonical schema
3. STORE     sql/schema.sql    → 5 normalized tables in SQLite (fip.db)
4. SEMANTIC  sql/views.sql     → documented SQL views = the business logic (THE PRODUCT)
5. DELIVERY  app/dashboard.py  → reads the views (so does Tableau) · tableau_export/*.csv
```

Business logic lives **only** in layer 4. Layers 1–3 just get clean data into one
place; layer 5 is disposable. Swap Streamlit for Tableau and nothing upstream changes.

---

## Quickstart (under 2 minutes, no cloud, no Docker)

```bash
pip install -r requirements.txt
make demo          # seed → build → reconcile → export → launch dashboard
```

`make demo` runs the whole pipeline and serves the dashboard at
`http://localhost:8501`. Prefer the pieces individually:

```bash
make pipeline      # build everything, no dashboard (writes fip.db, RECONCILIATION.md, tableau_export/)
make dashboard     # serve the dashboard against an existing build
make test          # run the test suite
```

---

## How SQL and Tableau interact (the handoff pattern)

**SQL is the extraction + semantic layer. Tableau is the delivery layer.** They meet
at the views — and only at the views.

- All business logic is a SQL view in [`sql/views.sql`](sql/views.sql). Each view
  carries a comment block stating the **business question**, **who asks it**, and the
  **refresh cadence**.
- In production, Tableau connects **live** to these views (or pulls scheduled
  extracts). Our local Streamlit dashboard reads the *exact same views*. That's the
  proof: the front end is interchangeable.
- `make pipeline` also writes [`tableau_export/`](tableau_export/) — one clean CSV per
  view. The demo line is: *"this folder is exactly what I'd point Tableau at on day one."*

### Worked example — the question → the view → the chart

> **Question (from the COO):** *"Which sites will run out of floor space, and when?"*

**The view** — [`vw_capacity_collision`](sql/views.sql) takes each site's MRP demand,
measures its quarter-over-quarter growth in demanded square footage, projects it
forward, and computes the **calendar quarter** in which demand crosses 85% of the
building (the point where you start a lease/expansion before hitting 100%):

```sql
SELECT site_name, current_util_pct, projected_util_2q_pct,
       projected_breach_quarter, collision_status
FROM   vw_capacity_collision
WHERE  collision_status = 'COLLISION WARNING';
```

**The answer it returns:**

| site_name | current_util_pct | projected_util_2q_pct | projected_breach_quarter | collision_status |
|---|---|---|---|---|
| Phoenix Production Line | 75.0 | 85.0 | **2026-Q2** | COLLISION WARNING |

**The chart** — the dashboard renders this as a red alert banner at the top:
*"Phoenix Production Line — demand growing ~10,000 sq ft/quarter, projected to cross
85% of the building in **2026-Q2**."* That is ~2 quarters of runway to act.

---

## The data model (5 tables)

| Table | System of record | What it holds |
|---|---|---|
| `sites` | canonical registry | one row per facility (sq ft, seats, type, status, provenance) |
| `leases` | real-estate / finance | annual rent + opex → drives $/sq ft |
| `headcount_snapshots` | **HRIS** | assigned headcount per site/program/quarter |
| `production_demand` | **MRP** | planned units × sq ft/unit → demanded floor space |
| `quality_issues` | **ERP/CMMS** + intake form | quality/safety/facility issues, severity, status |

See [`sql/schema.sql`](sql/schema.sql) for the full definitions and `etl_exceptions`,
the quarantine table where un-reconcilable rows go (nothing is silently dropped).

## The semantic layer (5 views — the product)

| View | Answers |
|---|---|
| `vw_quality_by_site_quarter` | Which sites have quality problems, trending how? |
| `vw_cost_per_sqft` | All-in cost per square foot, per site (null-safe for buildouts) |
| `vw_headcount_vs_seats` | Over capacity vs. paying for empty seats |
| `vw_capacity_vs_demand` | MRP-demanded floor space vs. what the building has |
| `vw_capacity_collision` | **Which sites hit the wall, and in which quarter** ★ |

## Scrappy → scalable: `ask.py`

One-off questions on a short timeline, straight against the views:

```bash
python -m fip.ask quality --site huntsville          # quality at one site
python -m fip.ask cost                                # cost/sqft across the portfolio
python -m fip.ask seats --site costa-mesa --quarter 2025-Q4
python -m fip.ask collision                           # the at-risk list
```

When a one-off proves useful, **promote it to a permanent view** with one flag — it
gets appended to `sql/views.sql` (with a comment block) and registered in the DB, so
the scrappy query becomes part of the product the dashboard and Tableau can use:

```bash
python -m fip.ask collision --site phoenix-line --promote at_risk_sites
```

## Acquisition integration (the "special projects" muscle)

The `quantico-acq` site arrives the way acquired data really does — a dump with its
own column names, mismatched site codes (`QNTC` / `quantico` / `Quantico Acq.`), a
NULL square footage, a rent formatted as `"$1,200,000"`, and a duplicate row
denominated in CAD. The ETL reconciles what it safely can and routes the rest to an
**exceptions queue** for a human. Every run regenerates
[`RECONCILIATION.md`](RECONCILIATION.md) — auto-reconciled actions, duplicate
resolutions, and the queue of items needing a decision.

---

## 3-minute demo walkthrough

1. **`make demo`** → "One command seeds simulated ERP/MRP/HRIS exports, cleans them,
   and serves the dashboard. No cloud, no install beyond pip."
2. **Top of the dashboard — the red banner.** "The collision detector flags Phoenix:
   it'll cross 85% floor utilization in **2026-Q2**, ~2 quarters out. That's the
   predictive layer — we see the wall before we hit it."
3. **Quality + cost tiles.** "Huntsville is our quality hotspot; Seattle is the cheap
   $/sq ft outlier at \$3.75; Boston is paying for empty seats while Costa Mesa's ramp
   blew past its desks." All from SQL views — zero logic in the dashboard.
4. **`RECONCILIATION.md`.** "The acquired Quantico site came in dirty. Here's what we
   auto-fixed and the two items queued for a human — the CAD/USD rent conflict and an
   orphan quality record."
5. **`tableau_export/`.** "These CSVs are one-per-view. On day one I point Tableau at
   the live views; the dashboard and Tableau read identical columns."

## Tests

```bash
make test     # 12 tests: ETL cleaning, view correctness, the dated collision warning
```

## Layout

```
sql/        schema.sql · views.sql            ← the model and the product
fip/        seed.py etl.py reconcile.py        ← build + reconcile
            db.py pipeline.py export.py ask.py
app/        dashboard.py                        ← Streamlit delivery layer
seeds/      *.csv  (generated source exports)
tableau_export/  *.csv  (generated per-view extracts)
tests/      test_etl.py test_views.py test_detector.py
```
