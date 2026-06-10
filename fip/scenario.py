"""
Scenario modeling — the DECISION-SUPPORT layer on top of the collision detector.

`vw_capacity_collision` answers "where does each site hit the wall, on which
constraint, at the CURRENT demand trend?" This module answers the next question a
VP actually asks: "...and what if a program grows faster than plan?" It takes the
base trend the view already computed and re-projects it under a per-site growth
multiplier, recomputing — live — which ceiling binds and in which quarter.

It is pure Python over plain dicts (the rows the view returns): no DB, no Streamlit.
That keeps the business logic here (and in SQL), never in the dashboard, and makes
it directly unit-testable. At multiplier 1.0 it reproduces the SQL view exactly.

The multiplier scales the quarter-over-quarter GROWTH of BOTH constraints (floor
sq ft and power kW) by the same factor. Consequences:
  • the projected breach quarter moves as the slider moves;
  • a site flips between 'none' (no projected collision) and a binding constraint
    as growth crosses the wall.
Because both constraints scale by the same factor, the order in which a single
site's two ceilings bind is invariant to its own multiplier — what changes is
WHEN it hits, and WHETHER it collides at all.

Also exposes `recommend_relocation`: given an at-risk site, find the best site
with enough slack on the binding constraint to absorb the overflow, preferring the
same region, then an adjacent one.
"""

WALL = 0.85  # you plan an expansion/upgrade before you hit 100% of a ceiling

# Region adjacency for relocation preference (symmetric). Loose US geography.
_ADJACENT = {
    "West":         {"Mountain", "Central"},
    "Mountain":     {"West", "Central", "South"},
    "Central":      {"West", "Mountain", "South", "Southeast", "Northeast"},
    "South":        {"Mountain", "Central", "Southeast"},
    "Southeast":    {"South", "Central", "Northeast", "Mid-Atlantic"},
    "Northeast":    {"Central", "Southeast", "Mid-Atlantic"},
    "Mid-Atlantic": {"Southeast", "Northeast"},
}


def _ceil_div_quarters(margin, growth):
    """Whole quarters until `margin` is consumed at `growth`/quarter.

    Mirrors the SQL ceil: CAST(margin/growth + 0.999999 AS INTEGER) (truncates
    toward zero, so it equals ceil() for the positive values used here)."""
    return int(margin / growth + 0.999999)


def _round1(x):
    """Round half away from zero to 1 dp, matching SQLite's ROUND(x, 1)."""
    if x is None:
        return None
    return float(int(x * 10 + (0.5 if x >= 0 else -0.5))) / 10


def project(capacity, demand, growth, last_q_index):
    """Project one constraint forward. Returns a dict mirroring the view's columns
    for that constraint: util now, util in 2 quarters, whole quarters to the wall,
    the dated breach quarter, and a status band. `growth` is per-quarter (already
    multiplied by any scenario factor)."""
    if capacity is None or capacity == 0:
        return {"util_pct": None, "projected_util_2q_pct": None,
                "quarters_to_wall": None, "breach_quarter": None,
                "status": "unknown — capacity data pending"}

    wall = WALL * capacity
    util = _round1(100.0 * demand / capacity)
    proj2q = _round1(100.0 * (demand + 2 * growth) / capacity)

    if growth <= 0:
        return {"util_pct": util, "projected_util_2q_pct": proj2q,
                "quarters_to_wall": None, "breach_quarter": None, "status": "stable"}

    if demand >= wall:
        qtw = 0
        status = "AT THE WALL NOW"
    else:
        raw = (wall - demand) / growth
        qtw = _ceil_div_quarters(wall - demand, growth)
        status = ("COLLISION WARNING" if raw <= 2 else
                  "watch" if raw <= 4 else "ok")

    q = last_q_index + qtw
    breach = f"{q // 4}-Q{q % 4 + 1}"
    return {"util_pct": util, "projected_util_2q_pct": proj2q,
            "quarters_to_wall": qtw, "breach_quarter": breach, "status": status}


def _bind(floor, power):
    """Pick the binding constraint = whichever wall is hit first. A None
    quarters_to_wall means that constraint has no projected collision."""
    fq, pq = floor["quarters_to_wall"], power["quarters_to_wall"]
    if fq is None and pq is None:
        chosen, name = floor, "none"          # neither collides
    elif pq is None:
        chosen, name = floor, "floor"
    elif fq is None:
        chosen, name = power, "power"
    elif pq <= fq:
        chosen, name = power, "power"          # tie -> power (the acute story)
    else:
        chosen, name = floor, "floor"
    return {
        "binding_constraint": name,
        "binding_quarters_to_wall": None if name == "none" else chosen["quarters_to_wall"],
        "binding_breach_quarter": None if name == "none" else chosen["breach_quarter"],
        "binding_status": chosen["status"],
    }


def _q_index(quarter_label):
    """'2025-Q4' -> absolute quarter index (year*4 + n - 1)."""
    year = int(quarter_label[:4])
    n = int(quarter_label.split("Q")[1])
    return year * 4 + n - 1


def scenario_row(row, multiplier=1.0):
    """Re-project one collision-view row under a growth `multiplier`. Returns a new
    dict with scenario floor_*/power_*/binding_* values. At multiplier 1.0 it
    reproduces the view's own columns."""
    last_q = _q_index(row["latest_quarter"])
    floor = project(row["available_sqft"], row["latest_demanded_sqft"],
                    (row["growth_sqft_per_quarter"] or 0) * multiplier, last_q)
    power = project(row["available_kw"], row["latest_demanded_kw"],
                    (row["growth_kw_per_quarter"] or 0) * multiplier, last_q)
    binding = _bind(floor, power)
    out = {
        "site_id": row["site_id"],
        "site_name": row["site_name"],
        "region": row.get("region"),
        "growth_multiplier": multiplier,
        "available_sqft": row["available_sqft"],
        "latest_demanded_sqft": row["latest_demanded_sqft"],
        "current_util_pct": floor["util_pct"],
        "projected_util_2q_pct": floor["projected_util_2q_pct"],
        "quarters_to_wall": floor["quarters_to_wall"],
        "projected_breach_quarter": floor["breach_quarter"],
        "collision_status": floor["status"],
        "available_kw": row["available_kw"],
        "latest_demanded_kw": row["latest_demanded_kw"],
        "power_util_pct": power["util_pct"],
        "projected_power_util_2q_pct": power["projected_util_2q_pct"],
        "power_quarters_to_wall": power["quarters_to_wall"],
        "power_breach_quarter": power["breach_quarter"],
        "power_status": power["status"],
    }
    out.update(binding)
    return out


def apply(rows, multipliers=None):
    """Apply per-site growth multipliers to all collision rows.

    `multipliers` is a {site_id: factor} dict (missing sites default to 1.0).
    Returns a list of scenario rows in the input order."""
    multipliers = multipliers or {}
    return [scenario_row(r, float(multipliers.get(r["site_id"], 1.0))) for r in rows]


def recommend_relocation(scenario_rows, at_risk_site_id):
    """Recommend the best site to shift overflow to, for an at-risk site.

    Looks at the at-risk site's BINDING constraint, computes how much projected
    (2-quarter) demand sits above the 85% wall, and finds the candidate site with
    enough slack below ITS wall on that same constraint to absorb it. Prefers the
    same region, then an adjacent region, then anywhere; within a tier, the site
    with the most slack. Returns a dict (or None if nothing qualifies)."""
    by_id = {r["site_id"]: r for r in scenario_rows}
    site = by_id.get(at_risk_site_id)
    if site is None or site["binding_constraint"] == "none":
        return None

    constraint = site["binding_constraint"]
    if constraint == "power":
        cap_key, dem_key, growth_unit = "available_kw", "latest_demanded_kw", "kW"
    else:
        cap_key, dem_key, growth_unit = "available_sqft", "latest_demanded_sqft", "sq ft"

    # how much we need to offload: projected 2q demand above this site's wall
    util2q = (site["projected_power_util_2q_pct"] if constraint == "power"
              else site["projected_util_2q_pct"])
    cap = site[cap_key]
    if cap is None or util2q is None:
        return None
    overflow = max(0.0, (util2q / 100.0) * cap - WALL * cap)
    if overflow <= 0:
        overflow = 1.0  # still recommend a destination even if just at the wall

    home = site.get("region")
    adjacent = _ADJACENT.get(home, set())

    def tier(region):
        if region == home:
            return 0
        if region in adjacent:
            return 1
        return 2

    candidates = []
    for r in scenario_rows:
        if r["site_id"] == at_risk_site_id:
            continue
        ccap, cdem = r.get(cap_key), r.get(dem_key)
        if ccap is None or cdem is None:
            continue                       # unknown capacity -> not a safe destination
        if r["binding_constraint"] != "none":
            continue                       # destination is itself at risk
        slack = WALL * ccap - cdem
        if slack >= overflow:
            candidates.append((tier(r.get("region")), -slack, r, slack))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, best, slack = candidates[0]
    return {
        "site_id": best["site_id"],
        "site_name": best["site_name"],
        "region": best.get("region"),
        "constraint": constraint,
        "unit": growth_unit,
        "overflow": round(overflow),
        "slack": round(slack),
        "same_region": best.get("region") == home,
    }
