import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fip import db, etl, seed  # noqa: E402


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    """Seed the CSVs and build an in-place SQLite DB once for the whole suite."""
    seed.main()
    conn = db.connect()
    db.apply_schema(conn)
    report = etl.load_all(conn)
    db.apply_views(conn)
    conn.commit()
    yield conn, report
    conn.close()
