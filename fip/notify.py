"""
Stakeholder notification drafts — copy-paste only, never auto-send.

When a capacity collision warning or a lease-cliff AT RISK flag is present, a VP
wants to forward a crisp heads-up to the decision owner. This module composes that
message as a plain, structured, copyable text block. It sends nothing: the
dashboard drops the text into a copy box and the human decides who gets it.

All the substance comes from the live views (the scenario rows and the lease-cliff
rows); this module only formats. Keeping it here — not in the dashboard — keeps the
delivery layer pure presentation and makes the wording unit-testable.
"""
from fip import scenario

_NO_OWNER = "[ASSIGN DECISION OWNER]"


def _recommended_action(site, scenario_rows, cliff):
    """Best single recommended next step for a site, drawn from the live analysis."""
    if site is not None and site["binding_constraint"] in ("floor", "power"):
        fix = ("Upgrade the electrical service (or shed/relocate load)"
               if site["binding_constraint"] == "power"
               else "Secure additional floor space (lease or expand)")
        rec = scenario.recommend_relocation(scenario_rows, site["site_id"])
        if rec:
            where = "same region" if rec["same_region"] else f"{rec['region']} region"
            fix += (f"; or shift ~{rec['overflow']:,} {rec['unit']} of overflow to "
                    f"{rec['site_name']} ({where}, {rec['slack']:,} {rec['unit']} slack)")
        return fix
    if cliff is not None and cliff["cliff_status"] == "AT RISK":
        return ("Make the lease renew/expand decision before the option deadline, "
                "informed by the projected capacity breach")
    return "Review at the next facilities sync"


def draft_alert(site_name, risk_type, binding_constraint, decision_needed,
                deadline, recommended_action, owner=None):
    """Format one structured, copyable stakeholder alert."""
    return (
        "STAKEHOLDER ALERT — Facilities Intelligence Platform\n"
        "----------------------------------------------------\n"
        f"Site:              {site_name}\n"
        f"Risk type:         {risk_type}\n"
        f"Binding constraint:{' ' + binding_constraint if binding_constraint else ' —'}\n"
        f"Decision needed:   {decision_needed}\n"
        f"Decision owner:    {owner or _NO_OWNER}\n"
        f"Deadline:          {deadline}\n"
        f"Recommended action:{' ' + recommended_action}\n"
        "----------------------------------------------------\n"
        "(Draft for review — copy, edit, and send manually. FIP sends nothing.)"
    )


def at_risk_sites(scenario_rows, cliff_rows):
    """Site ids with a live collision warning and/or a lease-cliff AT RISK flag,
    most-urgent collision first then cliff-only sites."""
    collision = {r["site_id"] for r in scenario_rows
                 if r["binding_status"] in ("COLLISION WARNING", "AT THE WALL NOW")}
    cliff = {r["site_id"] for r in cliff_rows if r["cliff_status"] == "AT RISK"}
    ordered = sorted(collision, key=lambda sid: next(
        r["binding_quarters_to_wall"] for r in scenario_rows if r["site_id"] == sid))
    for sid in sorted(cliff):
        if sid not in collision:
            ordered.append(sid)
    return ordered


def build_alerts(scenario_rows, cliff_rows, owners=None):
    """Build a stakeholder-alert draft for every at-risk site. Returns a list of
    dicts: {site_id, site_name, text}. `owners` optionally maps site_id -> owner."""
    owners = owners or {}
    by_site = {r["site_id"]: r for r in scenario_rows}
    cliff_by_site = {r["site_id"]: r for r in cliff_rows}
    alerts = []
    for sid in at_risk_sites(scenario_rows, cliff_rows):
        site = by_site.get(sid)
        cliff = cliff_by_site.get(sid)
        name = (site or cliff)["site_name"]

        risks = []
        if site and site["binding_status"] in ("COLLISION WARNING", "AT THE WALL NOW"):
            risks.append("Capacity collision")
        if cliff and cliff["cliff_status"] == "AT RISK":
            risks.append("Lease cliff")
        risk_type = " + ".join(risks)

        binding = (site["binding_constraint"].upper()
                   if site and site["binding_constraint"] in ("floor", "power") else "")

        if cliff and cliff["cliff_status"] == "AT RISK":
            deadline = (f"lease option deadline {cliff['lease_option_deadline']} "
                        f"({cliff['decision_window_days']} days before the "
                        f"{cliff['binding_breach_quarter']} breach)")
            decision = ("Commit to renew/expand the lease AND resolve the capacity "
                        "constraint before the option lapses")
        elif site:
            deadline = f"projected breach {site['binding_breach_quarter']}"
            decision = (f"Decide how to stay under the {site['binding_constraint']} "
                        f"ceiling before {site['binding_breach_quarter']}")
        else:
            deadline = "—"
            decision = "Review"

        alerts.append({
            "site_id": sid,
            "site_name": name,
            "text": draft_alert(
                site_name=name,
                risk_type=risk_type,
                binding_constraint=binding,
                decision_needed=decision,
                deadline=deadline,
                recommended_action=_recommended_action(site, scenario_rows, cliff),
                owner=owners.get(sid)),
        })
    return alerts
