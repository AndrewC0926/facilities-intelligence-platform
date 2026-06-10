"""
Database helpers. SQLite, zero-install. One place that knows where the DB lives,
how to connect, and how to (re)apply the schema and the SQL view layer.

The views are loaded from sql/views.sql verbatim — the same file a Tableau admin
would read to understand the semantic layer. Nothing reinterprets that SQL.
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(ROOT, "fip.db")
SCHEMA_SQL = os.path.join(ROOT, "sql", "schema.sql")
VIEWS_SQL = os.path.join(ROOT, "sql", "views.sql")


def connect(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _run_script(conn, path):
    with open(path) as f:
        conn.executescript(f.read())


def apply_schema(conn):
    """Drop & recreate all tables (clean rebuild for the demo)."""
    _run_script(conn, SCHEMA_SQL)


def apply_views(conn):
    """(Re)create the semantic-layer views from sql/views.sql."""
    _run_script(conn, VIEWS_SQL)


def query(conn, sql, params=()):
    """Run a read query and return a list of dict rows (handy for tests/CLI/export)."""
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
