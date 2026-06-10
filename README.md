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

## The data model (6 tables)

| Table | System of record | What it holds |
|---|---|---|
| `sites` | canonical registry | one row per facility (sq ft, seats, **power kW**, type, status, **lease dates**, provenance) |
| `leases` | real-estate / finance | annual rent + opex → drives $/sq ft |
| `headcount_snapshots` | **HRIS** | assigned headcount per site/program/quarter |
| `production_demand` | **MRP** | planned units × sq ft/unit **and × kW/unit** → demanded floor space + power |
| `quality_issues` | **ERP/CMMS** + intake form | quality/safety/facility issues, severity, status |
| `actions` | workflow layer | owned, dated tasks generated from insights (collision / reconciliation / quality) |

See [`sql/schema.sql`](sql/schema.sql) for the full definitions and `etl_exceptions`,
the quarantine table where un-reconcilable rows go (nothing is silently dropped).

## The semantic layer (the product)

| View | Answers |
|---|---|
| `vw_quality_by_site_quarter` | Which sites have quality problems, trending how? |
| `vw_cost_per_sqft` | All-in cost per square foot, per site (null-safe for buildouts) |
| `vw_headcount_vs_seats` | Over capacity vs. paying for empty seats |
| `vw_capacity_vs_demand` | MRP-demanded floor space **and power** vs. what the building has |
| `vw_capacity_collision` | **Which sites hit the wall, on which constraint, and in which quarter** ★ |
| `vw_reconciliation_status` | How many sites folded in, how many exceptions still open |
| `vw_open_actions` | What insights became owned, dated work — and what's still open |
| `vw_lease_cliff` | Where the lease option deadline and the capacity breach collide |
| `vw_site_health` | One composite 0–100 health score per site, with its four drivers |

---

## Workflow layer (Phase 2 — reporting → deciding → *doing*)

Four features turn the analytics into a tracked workflow:

1. **Actions table.** Every insight that needs a human becomes a trackable, **owned,
   dated** row in `actions` (source = collision / reconciliation / quality). The
   Actions tab color-codes open items by age (🟢 <30d · 🟡 30–60d · 🔴 >60d); the
   age logic lives in [`fip/actions.py`](fip/actions.py) with an injectable `today`
   so the seed stays deterministic.
2. **Lease cliff calendar.** `vw_lease_cliff` maps each site's binding breach quarter
   to a date and computes `decision_window_days` between the lease **option deadline**
   and that breach. `< 180 days ⇒ AT RISK` — Phoenix's option deadline is set 60 days
   before its 2026-Q1 power breach, so the real-estate and capacity decisions collide.
3. **Site health score.** `vw_site_health` is a composite 0–100, the equal-weight
   average of capacity headroom, quality, cost efficiency (vs the portfolio **median**
   $/sqft, computed in SQL), and data completeness. The scorecard ranks sites and
   expands to show each component.
4. **Stakeholder alert draft.** When a collision warning or a lease-cliff AT RISK flag
   is live, one button drafts a structured, **copy-paste** heads-up (site, risk type,
   binding constraint, decision needed, owner, deadline, recommended action) via
   [`fip/notify.py`](fip/notify.py). It sends nothing — a human decides who gets it.

The exec brief ([`python -m fip.brief`](fip/brief.py)) now also reports the open-actions
count and the oldest unresolved item's age.

---

## Decision support (reporting → deciding)

Three features turn the platform from "here's what's happening" into "here's what to do":

1. **Scenario modeling.** A per-site growth-multiplier slider (0×–3×) in the sidebar
   re-projects demand on top of the trend the collision view computes. The multiplier
   scales **both** the floor-sqft and power-kW growth, so the projected breach quarter
   moves live and a site flips between *no collision* and a binding constraint as growth
   crosses the wall. Below each warning, FIP recommends the best **relocation candidate** —
   a site with enough slack on the binding constraint, preferring the same or an adjacent
   region. All of this math lives in [`fip/scenario.py`](fip/scenario.py); the dashboard
   only collects slider inputs and renders.

2. **Multi-constraint capacity.** Every site now carries a `power_kw_capacity`, and MRP
   demand carries `kw_per_unit`, so `vw_capacity_collision` projects **two** ceilings —
   floor square footage and electrical power — and reports the **binding** one (whichever
   is hit first, and when). Phoenix is tuned so at 1× it breaches **POWER in 2026-Q1**,
   one quarter *before* its floor space runs out in 2026-Q2 — the whole point: the cheap
   constraint to miss is the one you weren't watching.

3. **Exec brief generator.** One button renders a dated one-page Markdown brief from the
   live views — headline risk, the binding-constraint forecast table, the acquisition
   reconciliation status with its open-exceptions count, and recommended actions
   (including the relocation option). Download it as `.md`, or run it headless:

   ```bash
   python -m fip.brief                 # print the dated brief
   python -m fip.brief --out brief.md  # write it to a file
   ```

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
make test     # 46 tests: ETL cleaning, view correctness, the dated collision warning,
              # multi-constraint binding logic, the scenario layer, the exec brief,
              # plus the workflow layer (actions/age, lease cliff, site health, alerts)
```

## Layout

```
sql/        schema.sql · views.sql            ← the model and the product
fip/        seed.py etl.py reconcile.py        ← build + reconcile
            db.py pipeline.py export.py ask.py
            scenario.py brief.py               ← decision-support logic (scenarios + brief)
            actions.py notify.py               ← workflow logic (action age + stakeholder alerts)
app/        dashboard.py                        ← Streamlit delivery layer
seeds/      *.csv  (generated source exports)
tableau_export/  *.csv  (generated per-view extracts)
tests/      test_etl.py test_views.py test_detector.py
            test_binding.py test_brief.py
            test_actions.py test_lease_cliff.py test_health.py test_notify.py
```
