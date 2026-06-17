import psycopg2
from psycopg2.extras import RealDictCursor
from .config import DATABASE_URL


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def fetchall(query, params=None):
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params or ())
        return cur.fetchall()


def fetchone(query, params=None):
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params or ())
        return cur.fetchone()


def execute(query, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params or ())
        conn.commit()
