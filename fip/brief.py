"""
Exec brief generator — the DECISION-SUPPORT one-pager.

Renders a dated, one-page Markdown brief straight from the live semantic views:
the headline capacity risk (on its binding constraint), the full collision
forecast table, the acquisition-reconciliation status with its open-exceptions
count, and a short list of recommended actions (including a relocation target for
the at-risk site). No business logic of its own — it reads `vw_capacity_collision`
and `vw_reconciliation_status` and composes the scenario layer in `fip.scenario`.

Two front doors, same output:
  • the dashboard's "Generate exec brief" button (offers a .md download), and
  • the CLI:  python -m fip.brief        (prints the brief; --out writes a file)

Self-bootstraps: if there is no database yet (bare clone), it builds one first,
exactly like the dashboard's first-boot path.
"""
import argparse
import datetime
import os

from fip import actions, db, scenario


def _ensure_db():
    if not os.path.exists(db.DB_PATH):
        from fip import pipeline
        pipeline.run()


def _fmt_pct(v):
    return "—" if v is None else f"{v:.1f}%"


def _table(rows):
    """Collision forecast as a Markdown table, ordered most-urgent first."""
    header = ("| Site | Binding constraint | Breach quarter | Status | "
              "Floor util | Power util |\n"
              "|---|---|---|---|---|---|")
    def sort_key(r):
        q = r["binding_quarters_to_wall"]
        return (q if q is not None else 9999, r["site_name"])
    lines = [header]
    for r in sorted(rows, key=sort_key):
        binding = r["binding_constraint"]
        binding_disp = binding.upper() if binding in ("floor", "power") else "—"
        lines.append(
            f"| {r['site_name']} | {binding_disp} | "
            f"{r['binding_breach_quarter'] or '—'} | {r['binding_status']} | "
            f"{_fmt_pct(r['current_util_pct'])} | {_fmt_pct(r['power_util_pct'])} |")
    return "\n".join(lines)


def _headline(at_risk):
    if not at_risk:
        return "**No imminent capacity collisions.** Every site is within its floor and power ceilings on the current demand trend."
    top = at_risk[0]
    constraint = top["binding_constraint"].upper()
    return (f"**{top['site_name']} hits the wall first — on {constraint} — in "
            f"{top['binding_breach_quarter']}.** "
            f"Floor utilization {_fmt_pct(top['current_util_pct'])}, "
            f"power utilization {_fmt_pct(top['power_util_pct'])} and climbing. "
            f"This is the binding constraint: it caps output before floor space does."
            if constraint == "POWER" else
            f"**{top['site_name']} hits the wall first — on {constraint} — in "
            f"{top['binding_breach_quarter']}.** "
            f"Floor utilization {_fmt_pct(top['current_util_pct'])}, "
            f"power utilization {_fmt_pct(top['power_util_pct'])} and climbing.")


def _actions(at_risk, rows, recon, exceptions):
    """Numbered action list; relocation options hang off their site as sub-bullets."""
    lines = []
    n = 0
    for r in at_risk:
        constraint = r["binding_constraint"]
        fix = ("upgrade the electrical service (or shed load)" if constraint == "power"
               else "secure additional floor space (lease/expand)")
        n += 1
        lines.append(
            f"{n}. **{r['site_name']}** — {fix} before **{r['binding_breach_quarter']}** "
            f"to stay under the {constraint} ceiling.")
        rec = scenario.recommend_relocation(rows, r["site_id"])
        if rec:
            where = "same region" if rec["same_region"] else f"{rec['region']} region"
            lines.append(
                f"   - Relocation option: shift ~{rec['overflow']:,} {rec['unit']} of "
                f"{r['site_name']}'s overflow to **{rec['site_name']}** ({where}, "
                f"{rec['slack']:,} {rec['unit']} of slack below its wall).")
    if exceptions:
        n += 1
        lines.append(
            f"{n}. **Reconciliation** — clear the **{recon['open_exceptions']}** open "
            f"item(s) in the exceptions queue (need a human decision): "
            + "; ".join(e["reason"].split(":")[0] for e in exceptions) + ".")
    if not lines:
        lines.append("1. No action required this cycle — monitor the weekly MRP feed.")
    return "\n".join(lines)


def render(conn=None, today=None, multipliers=None):
    """Build the brief as a Markdown string from the live views."""
    own = conn is None
    if own:
        _ensure_db()
        conn = db.connect()
    try:
        today = today or datetime.date.today()
        base = db.query(conn, "SELECT * FROM vw_capacity_collision")
        rows = scenario.apply(base, multipliers or {})
        recon = db.query(conn, "SELECT * FROM vw_reconciliation_status")[0]
        exceptions = db.query(
            conn, "SELECT source_file, reason FROM etl_exceptions ORDER BY exception_id")
        action_summary = actions.summary(conn, today)
        pipeline = db.query(conn, "SELECT * FROM vw_integration_pipeline")
    finally:
        if own:
            conn.close()

    integrating = sum(1 for p in pipeline if p["site_status"] == "acquired_integrating")
    below_80 = sum(1 for p in pipeline if p["completeness_pct"] < 80)
    stalled = sum(1 for p in pipeline if p["stalled_flag"])

    at_risk = sorted(
        [r for r in rows if r["binding_status"] in ("COLLISION WARNING", "AT THE WALL NOW")],
        key=lambda r: (r["binding_quarters_to_wall"]
                       if r["binding_quarters_to_wall"] is not None else 9999, r["site_name"]))

    scenario_note = ""
    if multipliers:
        active = {k: v for k, v in multipliers.items() if float(v) != 1.0}
        if active:
            scenario_note = ("\n> _Scenario applied: growth multipliers "
                             + ", ".join(f"{k}×{v:g}" for k, v in active.items()) + "._\n")

    md = f"""# Facilities Intelligence — Executive Brief
_Generated {today.isoformat()} from the live semantic views (`vw_capacity_collision`, `vw_reconciliation_status`, `vw_open_actions`, `vw_integration_pipeline`)._
{scenario_note}
## Headline risk

{_headline(at_risk)}

## Capacity collision forecast

Each site is projected on **two** ceilings — floor square footage and electrical
power. The binding constraint is whichever wall is hit first.

{_table(rows)}

## Acquisition reconciliation

- **{recon['acquired_sites']}** acquired site(s) reconciled from a dirty import.
- **{recon['open_exceptions']}** item(s) in the exceptions queue awaiting a human decision:
"""
    for e in exceptions:
        md += f"  - `{e['source_file']}` — {e['reason']}\n"
    md += (f"- Integration pipeline: **{integrating}** site(s) in active integration, "
           f"**{below_80}** below 80% data completeness"
           + (f" (**{stalled}** stalled >12 mo)" if stalled else "") + ".\n")

    if action_summary["oldest_age_days"] is not None:
        aging = (f"the oldest unresolved item is **{action_summary['oldest_age_days']} days** "
                 f"old ({action_summary['oldest_site']} — {action_summary['oldest_title']})")
    else:
        aging = "no aging items"
    md += f"""
## Action tracker

- **{action_summary['open_count']}** open action(s) being tracked; {aging}.

## Recommended actions

{_actions(at_risk, rows, recon, exceptions)}

---
_FIP decision-support brief · all figures trace to SQL views · regenerate any time._
"""
    return md


def main(argv=None):
    p = argparse.ArgumentParser(prog="fip.brief",
                                description="Generate the dated executive brief from the live views.")
    p.add_argument("--out", metavar="PATH", help="write the brief to PATH instead of stdout")
    args = p.parse_args(argv)
    md = render()
    if args.out:
        with open(args.out, "w") as f:
            f.write(md)
        print(f"Exec brief written to {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
