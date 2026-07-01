"""Occupancy & seat-demand layer — assert the PATTERNS (which space type binds,
null-safety, the bottleneck iff, and full genericity), never one site's story."""
from fip import db, etl


def _binding(conn):
    return {r["site_id"]: r for r in
            db.query(conn, "SELECT * FROM vw_space_collision WHERE is_binding = 1")}


# --- pattern: desks are rarely the binding constraint at industrial sites --------

def test_industrial_site_binds_on_a_non_desk_space_type(built_db):
    conn, _ = built_db
    arsenal = _binding(conn)["arsenal-campus"]
    assert arsenal["space_type"] == "parking_stall"   # production is parking-heavy, 0 desks
    assert arsenal["space_type"] != "desk"


def test_mixed_site_binds_on_scif_seat(built_db):
    conn, _ = built_db
    hq = _binding(conn)["hq-flagship"]
    assert hq["space_type"] == "scif_seat"             # cleared staff, ample floor/power


def test_office_site_binds_on_desk(built_db):
    conn, _ = built_db
    seattle = _binding(conn)["seattle-hub"]
    assert seattle["space_type"] == "desk"             # the one place the office playbook applies


# --- null-safety: pending / planned capacity never produce a false breach --------

def test_audit_pending_capacity_reports_data_pending_not_breach(built_db):
    conn, _ = built_db
    scif = db.query(conn,
        "SELECT * FROM vw_space_collision WHERE site_id='space-domain' AND space_type='scif_seat'")[0]
    assert scif["capacity"] is None
    assert scif["capacity_status"] == "audit_pending"
    assert "data pending" in scif["space_status"]
    assert scif["quarters_to_wall"] is None            # never a false breach
    assert scif["breach_quarter"] is None


def test_planned_capacity_reports_supportable_headcount_not_breach(built_db):
    conn, _ = built_db
    desk = db.query(conn,
        "SELECT * FROM vw_space_collision WHERE site_id='long-beach' AND space_type='desk'")[0]
    assert desk["capacity_status"] == "planned"
    assert desk["space_status"] == "planned supply"
    assert desk["quarters_to_wall"] is None            # not a breach
    assert desk["supportable_units"] is not None       # reports future supportable supply


# --- the bottleneck flag: fires IFF space lead time exceeds people fill time ------

def test_bottleneck_flag_fires_iff_space_lead_exceeds_fill_time(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_time_to_seat")
    assert rows, "time-to-seat view should not be empty"
    for r in rows:
        expected = (r["time_to_seat_days"] is not None
                    and r["time_to_seat_days"] > r["time_to_fill_days"])
        fired = r["bottleneck_flag"] == "facilities_bottleneck"
        assert fired == expected, (r["site_id"], r["archetype"],
                                   r["time_to_seat_days"], r["time_to_fill_days"], r["bottleneck_flag"])


def test_the_office_site_is_not_a_facilities_bottleneck(built_db):
    conn, _ = built_db
    seattle = db.query(conn, "SELECT * FROM vw_time_to_seat WHERE site_id='seattle-hub'")[0]
    # desks (30d) build faster than engineers hire (75d) -> facilities is NOT the cap
    assert seattle["bottleneck_flag"] == "ok"


# --- classified mode (ICD 705): SCIF space is flagged restricted_sensing ----------

def test_scif_is_flagged_restricted_sensing_others_are_not(built_db):
    conn, _ = built_db
    types = {r["name"]: r["restricted_sensing"] for r in db.query(conn, "SELECT * FROM space_types")}
    assert types["scif_seat"] == 1        # accredited: sensor occupancy unavailable
    assert types["desk"] == 0
    # the demand view surfaces the flag so downstream can honor the degradation
    scif_demand = db.query(conn, "SELECT DISTINCT restricted_sensing FROM vw_space_demand WHERE space_type='scif_seat'")
    assert scif_demand and scif_demand[0]["restricted_sensing"] == 1


# --- plan reconciliation surfaces the three-plan disagreement ---------------------

def test_plan_reconciliation_flags_where_space_cannot_support_the_pipeline(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_plan_reconciliation")}
    hq = rows["hq-flagship"]
    # cleared hiring is silently capped: space supports fewer than the pipeline implies
    assert hq["space_supportable_headcount"] < hq["pipeline_implied_headcount"]
    assert hq["delta_supportable_vs_pipeline"] < 0


# --- genericity: a brand-new site, added via DATA ONLY, flows through every view ---

def test_a_brand_new_site_flows_through_every_view_with_no_code_change(tmp_path):
    dbp = str(tmp_path / "nova.db")
    conn = db.connect(dbp)
    db.apply_schema(conn)
    etl.load_all(conn)          # loads the standard seeds
    db.apply_views(conn)
    # A site that does not exist in any Python or SQL file — configured purely as data,
    # with a binding space type (bench) that no seeded site uses. If the layer is truly
    # data-driven, it flows through demand -> collision -> time-to-seat -> reconciliation.
    conn.executescript("""
        INSERT INTO sites (site_id, site_name, region, site_type, status, site_status, source_system)
          VALUES ('nova-lab', 'Nova Lab', 'West', 'office', 'operational', 'operational', 'canonical');
        INSERT INTO headcount_snapshots (snapshot_id, site_id, quarter, program, archetype, headcount) VALUES
          (90001, 'nova-lab', '2025-Q4', 'Nova', 'engineer', 100),
          (90002, 'nova-lab', '2026-Q1', 'Nova', 'engineer', 140),
          (90003, 'nova-lab', '2026-Q2', 'Nova', 'engineer', 180),
          (90004, 'nova-lab', '2026-Q3', 'Nova', 'engineer', 220);
        INSERT INTO space_capacity VALUES ('nova-lab', 2, 60,   'confirmed');   -- bench, tight
        INSERT INTO space_capacity VALUES ('nova-lab', 1, 5000, 'confirmed');   -- desk, ample
        INSERT INTO requisition_pipeline VALUES (90001, 'nova-lab', 2, '2026-Q3', 50, 40);
    """)
    conn.commit()

    assert any(r["site_id"] == "nova-lab" for r in db.query(conn, "SELECT DISTINCT site_id FROM vw_space_demand"))
    binding = db.query(conn, "SELECT space_type FROM vw_space_collision WHERE site_id='nova-lab' AND is_binding=1")
    assert binding and binding[0]["space_type"] == "bench"   # engineer bench ratio, tight capacity -> binds
    assert any(r["site_id"] == "nova-lab" for r in db.query(conn, "SELECT site_id FROM vw_time_to_seat"))
    assert any(r["site_id"] == "nova-lab" for r in db.query(conn, "SELECT site_id FROM vw_plan_reconciliation"))
    conn.close()
