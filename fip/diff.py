"""
Change-impact diff — "if program X targets N units/quarter, what moves?"

Recomputes the capacity collision via the SAME scenario logic the dashboard uses
(fip.scenario), then prints a before/after table of every breach date, binding
constraint, and cost-of-delay figure that CHANGED. Everything not downstream of the
change stays put — the point is to isolate one decision's blast radius.

    python -m fip.diff --program "Fury CCA" --target 74

A program's new target scales its primary site's demand growth by target / current
target; scenario.apply then re-derives that site's breaches. Only the affected site
can move; the diff shows exactly what did.
"""
import argparse

from fip import db, scenario


def compute(conn, program, target):
    """Return (rows, meta). rows: one dict per site with before/after figures and a
    `changed` flag. meta: the affected site and the applied multiplier."""
    prog = db.query(conn, "SELECT * FROM programs WHERE program_name = ?", (program,))
    if not prog:
        raise SystemExit(f"no such program: {program!r}")
    prog = prog[0]
    site = prog["primary_site_id"]
    baseline_target = prog["units_per_quarter_target"] or prog["units_per_quarter_current"]
    if not baseline_target:
        raise SystemExit(f"program {program!r} has no baseline target to scale from")
    multiplier = float(target) / float(baseline_target)

    base = db.query(conn, "SELECT * FROM vw_capacity_collision")
    before = {r["site_id"]: r for r in scenario.apply(base, {})}
    after = {r["site_id"]: r for r in scenario.apply(base, {site: multiplier})}
    delay = {r["site_id"]: r["delay_cost_per_quarter_usd"]
             for r in db.query(conn, "SELECT * FROM vw_cost_of_delay")}

    def cost_of_delay(row):
        dc, qtw = delay.get(row["site_id"]), row["binding_quarters_to_wall"]
        return round(dc * qtw) if (dc is not None and qtw is not None) else None

    rows = []
    for sid in sorted(before):
        b, a = before[sid], after[sid]
        cod_b, cod_a = cost_of_delay(b), cost_of_delay(a)
        changed = (b["binding_constraint"] != a["binding_constraint"]
                   or b["binding_breach_quarter"] != a["binding_breach_quarter"]
                   or cod_b != cod_a)
        rows.append({
            "site_id": sid, "site_name": b["site_name"],
            "constraint_before": b["binding_constraint"], "constraint_after": a["binding_constraint"],
            "breach_before": b["binding_breach_quarter"], "breach_after": a["binding_breach_quarter"],
            "cost_of_delay_before": cod_b, "cost_of_delay_after": cod_a,
            "changed": changed,
        })
    return rows, {"site": site, "multiplier": multiplier, "baseline_target": baseline_target}


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"${v:,.0f}"
    return str(v)


def render(rows, meta, program, target):
    changed = [r for r in rows if r["changed"]]
    out = []
    out.append(f"Change impact — {program}: target -> {target}/qtr "
               f"(x{meta['multiplier']:.2f} on {meta['site']}, from {meta['baseline_target']}/qtr)")
    out.append("=" * 78)
    if not changed:
        out.append("No breach date, binding constraint, or cost-of-delay figure changed.")
    else:
        out.append(f"{'Site':22} {'Constraint':16} {'Breach':16} {'Cost of delay':22}")
        out.append("-" * 78)
        for r in changed:
            con = (f"{r['constraint_before']}→{r['constraint_after']}"
                   if r["constraint_before"] != r["constraint_after"] else r["constraint_after"] or "—")
            br = (f"{r['breach_before']}→{r['breach_after']}"
                  if r["breach_before"] != r["breach_after"] else r["breach_after"] or "—")
            cod = (f"{_fmt(r['cost_of_delay_before'])}→{_fmt(r['cost_of_delay_after'])}"
                   if r["cost_of_delay_before"] != r["cost_of_delay_after"]
                   else _fmt(r["cost_of_delay_after"]))
            out.append(f"{r['site_name'][:22]:22} {con:16} {br:16} {cod:22}")
    unchanged = len(rows) - len(changed)
    out.append("-" * 78)
    out.append(f"{len(changed)} site(s) moved; {unchanged} unchanged (nothing else moved).")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="fip.diff", description="Change-impact diff for a program target change.")
    p.add_argument("--program", required=True, help="program_name to change")
    p.add_argument("--target", required=True, type=int, help="new units_per_quarter target")
    args = p.parse_args(argv)
    conn = db.connect()
    try:
        rows, meta = compute(conn, args.program, args.target)
    finally:
        conn.close()
    print(render(rows, meta, args.program, args.target))


if __name__ == "__main__":
    main()
