# Facilities Intelligence Platform (FIP)

**One source of truth for a multi-site hardware company's facilities portfolio —
and the decisions that come out of it.**

Most facilities organizations answer a simple question — *"which site is about to
run out of room, and what do we do about it?"* — by emailing three teams and
stitching spreadsheets together for a week. By the time the answer arrives, the
window to act on it has often closed.

FIP collapses that into a single model. It ingests the exports your teams already
produce (ERP/CMMS, MRP, HRIS, and the messy data dump from a newly-acquired site),
reconciles them into one clean picture, and turns that picture into **dated
warnings, owned actions, and a one-page brief you can take into a staff meeting.**

It runs on a laptop in under two minutes. No cloud account, no Docker, no install
beyond `pip`.

---

## What it lets you answer — and decide

**See the portfolio clearly**
- Which sites have quality problems, and are they getting better or worse?
- Where are we over capacity (people with no desk) or paying for empty seats?
- What does each site really cost per square foot, all-in?
- One number per site: how healthy is it, and what's dragging it down?

**See the wall before you hit it**
- Which sites will outgrow their building — **on which constraint (floor space or
  electrical power), and in which calendar quarter?**
- Where does a lease decision deadline collide with a capacity breach, leaving too
  little time to act?
- What happens to all of the above if a program grows 2× or 3× faster than plan?

**Turn the answer into action**
- Every risk becomes a tracked item with an **owner and a due date**, color-coded by
  how long it's been sitting.
- One click drafts a **stakeholder alert** you can copy, edit, and send.
- One click produces a **dated executive brief** — headline risk, forecast, open
  items, and recommended moves — ready to forward.

The headline example today: **Arsenal Campus — the flagship mega-factory (Building 1
of 7 operational) — is projected to hit its power ceiling in 2026-Q4, one quarter
before it runs out of floor space (2027-Q1), and its lease option deadline falls
only 60 days before that wall.** Power, not square footage, is the binding
constraint, and the real-estate clock is already running. FIP surfaces all three
facts together, names the programs at risk (Fury CCA, Roadrunner, Barracuda, Bolt),
and recommends shifting overflow to HQ & Flagship Factory (same region, ample slack).

---

## How it's built (five layers)

```
1. SOURCES   simulated exports → seeds/*.csv      (ERP/CMMS · MRP · HRIS · acquired-site dump)
2. INGEST    fip/etl.py        → cleans & reconciles dirty data into one canonical model
3. STORE     sql/schema.sql    → 13 normalized tables in SQLite (fip.db)
4. SEMANTIC  sql/views.sql     → documented SQL views = the business logic (THE PRODUCT)
5. DELIVERY  app/dashboard.py  → reads the views (so does Tableau) · tableau_export/*.csv
```

The important idea for a leader to trust the numbers: **all of the business logic
lives in one place — layer 4, the SQL views.** Every figure on the dashboard, in
the brief, and in the Tableau extracts traces back to a documented view you can
read. The front end is disposable: swap our dashboard for Tableau and nothing
upstream changes. There is no hidden math in a spreadsheet or a slide.

---

## Run it locally (under two minutes)

```bash
pip install -r requirements.txt
make demo          # seed → build → reconcile → export → launch the dashboard
```

`make demo` builds everything and serves the dashboard at `http://localhost:8501`.
The dashboard is organized into tabs — Capacity & Scenario, Programs, Occupancy &
Seats, Actions, Lease Cliff, Site Health, Reconciliation, Quality & Cost, and a
30-second issue-intake form. It also self-bootstraps: point it at an empty checkout
and it builds its own database on first launch.

Prefer the pieces individually:

```bash
make pipeline      # build everything, no dashboard (writes the DB, the reconciliation
                   # report, and one clean CSV per view in tableau_export/)
make dashboard     # serve the dashboard against an existing build
make test          # run the full test suite (65 tests)
```

And two things you can run straight from the terminal:

```bash
python -m fip.brief                    # print the dated executive brief
python -m fip.ask collision            # ask a one-off question against the live model
```

---

## The semantic layer — what each view answers

These fifteen SQL views *are* the product. Each one carries a comment block stating the
business question, who asks it, and how often it refreshes.

| View | The question it answers |
|---|---|
| `vw_quality_by_site_quarter` | Which sites have quality problems, and is the trend improving or worsening? |
| `vw_cost_per_sqft` | What does each site cost per square foot, all-in? (safe for sites mid-buildout) |
| `vw_headcount_vs_seats` | Where are we over capacity vs. paying for empty seats? |
| `vw_capacity_vs_demand` | How much floor space **and power** does the production plan demand vs. what the building has? |
| `vw_capacity_collision` ★ | Which sites hit the wall, **on which constraint, and in which quarter?** |
| `vw_lease_cliff` | Where does a lease option deadline collide with a projected capacity breach? |
| `vw_site_health` | One composite 0–100 health score per site, with its four underlying drivers. |
| `vw_open_actions` | Which insights have become owned, dated work — and what's still open? |
| `vw_reconciliation_status` | How cleanly did the acquired site fold in, and how many items still need a human? |
| `vw_program_facility_risk` ★ | When a building hits a wall, **which programs does it stop, and how far short of target?** |
| `vw_integration_pipeline` | Which acquired/buildout sites are still being stood up, and how complete is their data? |
| `vw_space_demand` | How many units of each space type does each site demand, by archetype + pipeline, by quarter? |
| `vw_space_collision` ★ | Which **space type** runs out first at each site — parking, SCIF seats, benches, desks? |
| `vw_time_to_seat` | Where does building the seat take longer than hiring the person (facilities is the bottleneck)? |
| `vw_plan_reconciliation` | Where do authorized, pipeline-implied, and space-supportable headcount disagree? |

### Worked example — the question → the view → the decision

> **From the COO:** *"Which sites are about to run out of capacity, and how much
> runway do we have?"*

`vw_capacity_collision` takes each site's production plan, measures quarter-over-quarter
growth in **both** demanded floor space and demanded power, projects each forward,
and reports the quarter in which the **first** ceiling is crossed:

| Site | Binding constraint | Breach quarter | Floor util | Power util |
|---|---|---|---|---|
| Arsenal Campus | **POWER** | **2026-Q4** | 78% | 80% and climbing |

`vw_lease_cliff` then layers in the real-estate clock: Arsenal's lease option
deadline lands **60 days** before that breach — well inside the 180-day comfort
window — so the site is flagged **AT RISK**. `vw_program_facility_risk` names the
casualties — Fury CCA, Roadrunner, Barracuda and Bolt all build at Arsenal, so the
power wall caps their unit targets. The dashboard shows all of this as a red banner
and drafts a stakeholder alert; the exec brief writes it up. That is the difference
between *seeing* the wall and *deciding* before you hit it.

---

## The decision-support and workflow capabilities

Everything above is reporting. These are the capabilities that move FIP from
*reporting* to *deciding* to *doing* — added across two phases of work.

**Multi-constraint capacity detection.** A factory rarely runs out of just one
thing. FIP tracks two ceilings per site — floor square footage and electrical power
— and always reports the **binding** one (whichever is hit first). The cheap
constraint to miss is the one you weren't watching; for Arsenal, that's power.

**Scenario modeling.** A sidebar slider lets you scale any site's demand growth from
0× to 3×. The projected breach quarter, the binding constraint, and the
recommendations all recompute live — so you can pressure-test *"what if the Anvil
program ramps twice as fast?"* in the room, not in a follow-up.

**Relocation recommendations.** When a site is at risk, FIP names the best site to
absorb the overflow — one with enough slack on the binding constraint, preferring
the same or an adjacent region.

**Lease cliff calendar.** For every leased site, FIP computes the number of days
between the lease option deadline and the projected capacity breach. A window under
180 days is flagged AT RISK — that's where the real-estate decision and the capacity
decision collide and you can least afford to be surprised.

**Site health score.** One number, 0–100, per site — the equal-weight blend of
capacity headroom, quality, cost efficiency (versus the portfolio median), and data
completeness. The scorecard ranks the portfolio and expands to show exactly which of
the four drivers is pulling a site down.

**Action tracker.** Every insight that needs a human becomes a row with an **owner
and a due date**, color-coded by age (green under 30 days, yellow 30–60, red over
60). Nothing important quietly ages out of view.

**Stakeholder alerts.** When a collision or lease-cliff risk is live, one button
drafts a structured, copy-paste heads-up — site, risk type, binding constraint, the
decision needed, the owner, the deadline, and the recommended action. It sends
nothing; a human decides who gets it.

**Executive brief.** One button (or one command, `python -m fip.brief`) produces a
dated one-page brief from the live views: headline risk, the binding-constraint
forecast, the acquisition reconciliation status, the open-action count and the age
of the oldest unresolved item, and the recommended moves. Download it as Markdown
and forward it.

**Ask any one-off, then make it permanent.** For questions on a short timeline, query
the model directly from the terminal — and promote a useful query into a permanent,
named view with a single flag:

```bash
python -m fip.ask quality --site maritime-systems     # quality at one site
python -m fip.ask cost                                 # cost/sqft across the portfolio
python -m fip.ask seats --site seattle-hub --quarter 2026-Q3
python -m fip.ask collision --site arsenal-campus --promote at_risk_sites
```

---

## Folding in an acquired site (the "special projects" muscle)

Acquisitions never arrive clean. The recently-acquired `advanced-imaging` site
(Advanced Imaging Facility) comes in the way real acquired data does — its own
column names, different spellings of the site code (`AIF` / `advanced imaging`), a
missing square footage, an un-audited power capacity, a rent formatted as
`"$1,200,000"`, and a duplicate row denominated in Canadian dollars.

FIP reconciles everything it safely can and routes the rest to an **exceptions
queue** for a human — nothing is silently dropped or silently "fixed." Today that
queue holds exactly **two** items awaiting a decision: the USD-vs-CAD rent conflict,
and an orphaned quality record pointing at a site (`kona-test-range`) that exists in
no registry. Newer acquisitions still being folded in are tracked to completion in
`vw_integration_pipeline`. Every build regenerates [`RECONCILIATION.md`](RECONCILIATION.md) so the
audit trail is always current.

---

## Why you can trust the numbers

- **All logic is in the documented SQL views** — readable, commented, version-
  controlled. No spreadsheet macros, no per-slide arithmetic.
- **The dashboard and Tableau read the identical views.** `make pipeline` writes one
  clean CSV per view into [`tableau_export/`](tableau_export/) — *"this folder is
  exactly what I'd point Tableau at on day one."*
- **46 automated tests** cover the data cleaning, every view, the dated collision
  warning, the multi-constraint binding logic, the scenario math, the lease-cliff
  and health-score logic, and the brief — so a change that breaks a number is caught
  before it ships. Run `make test`.

---

## Phase 3: Occupancy

"Headcount" is not one number — it is **N demand curves.** A production operator, a
design engineer, a cleared analyst, a field tech, a contractor and a corporate hire
consume completely different space: the operator needs a workstation and two-thirds
of a parking stall but *zero* desks; the analyst needs an accredited SCIF seat that
takes **540 days** to stand up; the engineer needs a desk that takes 30. When
facilities plans desks off total headcount, it plans the wrong thing — at an
industrial site the wall you hit first is **parking or SCIF seats, never desks.**
The occupancy layer projects each space type on its own curve (the same 85%-wall,
dated-breach math as the capacity detector), reports the **binding** space type per
site, and compares the time to *build* a seat against the time to *hire* the person
who fills it — flagging every place where facilities, not recruiting, is the real
cap on growth. The hiring pipeline is the *leading* indicator; HRIS headcount is
trailing.

The entire layer is **configuration as data.** Archetypes, space types, the ratio of
each space a worker consumes, provisioning lead times, per-site capacities, and the
requisition pipeline all live in tables — nothing site-specific, and no ratio or
lead time, is hardcoded in a view or in Python. A facility that does not exist yet
flows through every view the moment its rows are seeded, with zero code changes; a
site with one space type is handled identically to one with ten. It is null-safe by
the same discipline as the rest of the platform: a SCIF whose accreditation audit is
pending reports *data pending* rather than a false breach, and a building still under
construction reports the headcount its *planned* capacity will support rather than a
collision. And it respects the classified boundary by design — for accredited space
(ICD 705) sensor-based occupancy is unavailable, so utilization degrades to headcount
and badge/booking counts, flagged in the data (`restricted_sensing`) so downstream
never assumes a sensor it cannot have.

---

## Roadmap

The platform today reasons over a largely **static plan**. The next steps make it
react to the business in real time and connect it to the money. (The Phase 3
requisition pipeline is a first step toward the first bullet.)

- **Upstream demand signals.** Replace the seeded MRP/HRIS snapshots with live feeds
  from the systems of record — program forecasts, the sales/booking pipeline, and
  hiring plans — so the demand trend updates as the business changes, and leading
  indicators (a new program win, a forecast revision) move the breach dates the day
  they happen rather than at the next planning cycle.

- **Capital planning layer.** Turn the collision, lease-cliff, and relocation
  outputs into a **costed, multi-year capital plan**: what to spend, where, and when;
  the avoided cost or downtime of acting early versus late; and the trade-off between
  expanding a building, upgrading its power service, signing a new lease, or shifting
  a program. Actions get tied to budget lines, and the portfolio can be optimized as
  a whole instead of one fire at a time.

Together these close the loop — from a live signal upstream, to a dated risk, to an
owned action, to a funded decision.

---

## The data model (13 tables)

The portfolio is **10 sites** — a flagship mega-factory (Arsenal Campus), a campus
under construction (Long Beach), a rocket-motor complex, an undersea-systems
factory, three acquired sites at different stages of integration, and two
engineering hubs — running **8 programs** (Fury CCA, Roadrunner, Barracuda, Ghost
Shark, ALTIUS, Bolt, SRM Supply, Lattice OS).

| Table | System of record | What it holds |
|---|---|---|
| `sites` | canonical registry | one row per facility — size, seats, **power capacity (kW)**, lifecycle status, integration & lease dates, provenance |
| `programs` | program registry | each program → its primary/secondary site, current vs. target quarterly output, per-unit floor & power footprint |
| `leases` | real-estate / finance | annual rent + opex → drives cost per square foot |
| `headcount_snapshots` | **HRIS** | assigned headcount per site/program/quarter, tagged by worker **archetype** |
| `production_demand` | **MRP** | per-quarter facility demand → demanded floor space + power (kW) |
| `quality_issues` | **ERP/CMMS** + intake form | quality/safety/facility issues, severity, status |
| `actions` | workflow layer | owned, dated tasks generated from insights (collision / reconciliation / quality) |
| `archetypes` | occupancy config | worker archetypes (production, engineer, cleared, field, contractor, corporate — any set) |
| `space_types` | occupancy config | space types + provisioning **lead times** + `restricted_sensing` (ICD 705) |
| `archetype_space_map` | occupancy config | how much of each space type one worker of an archetype consumes (ratios as data) |
| `space_capacity` | occupancy | per-site supply per space type, with `capacity_status` (confirmed / audit_pending / planned) |
| `requisition_pipeline` | **ATS** (leading) | open reqs + time-to-fill per site/archetype/quarter — the leading indicator |

See [`sql/schema.sql`](sql/schema.sql) for the full definitions, plus `etl_exceptions`,
the quarantine table where un-reconcilable rows wait for a human.

## Layout

```
sql/        schema.sql · views.sql              ← the model and the product (the SQL views)
fip/        seed.py etl.py reconcile.py          ← generate source data, clean it, reconcile it
            db.py pipeline.py export.py ask.py
            scenario.py brief.py                 ← decision support (scenarios + exec brief)
            actions.py notify.py                 ← workflow (action age + stakeholder alerts)
app/        dashboard.py                          ← the Streamlit front end (presentation only)
seeds/      *.csv   (the simulated source-system exports)
tableau_export/  *.csv   (one clean extract per view — the Tableau handoff)
tests/      65 tests across ingestion, every view, and all decision/workflow/occupancy logic
```
