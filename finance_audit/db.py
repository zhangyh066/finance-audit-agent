import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
    role TEXT NOT NULL, password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_centers (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, budget_cents INTEGER NOT NULL CHECK(budget_cents >= 0)
);
CREATE TABLE IF NOT EXISTS accounts (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'published')),
    source_name TEXT NOT NULL, source_text TEXT NOT NULL,
    created_at TEXT NOT NULL, published_at TEXT,
    UNIQUE(name, version)
);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies(id),
    code TEXT NOT NULL, name TEXT NOT NULL, expense_type TEXT NOT NULL,
    field TEXT NOT NULL, operator TEXT NOT NULL, value_json TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('error', 'warning')),
    message TEXT NOT NULL, sort_order INTEGER NOT NULL, UNIQUE(policy_id, code)
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, uploader_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
    extracted_text TEXT NOT NULL, fields_json TEXT NOT NULL,
    parser TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY, case_no TEXT UNIQUE NOT NULL, owner_id TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('draft', 'pending_review', 'returned', 'approved', 'rejected')),
    expense_type TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
    cost_center_code TEXT REFERENCES cost_centers(code),
    amount_cents INTEGER, invoice_date TEXT NOT NULL DEFAULT '',
    invoice_code TEXT NOT NULL DEFAULT '', invoice_number TEXT NOT NULL DEFAULT '',
    seller_name TEXT NOT NULL DEFAULT '', buyer_name TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    document_id TEXT REFERENCES documents(id), policy_id TEXT REFERENCES policies(id),
    submitted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
    policy_id TEXT NOT NULL REFERENCES policies(id), engine_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
    rule_code TEXT NOT NULL, rule_name TEXT NOT NULL, severity TEXT NOT NULL,
    passed INTEGER NOT NULL, actual_json TEXT NOT NULL, expected_json TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT REFERENCES cases(id),
    actor_id TEXT REFERENCES users(id), action TEXT NOT NULL,
    from_status TEXT, to_status TEXT, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT REFERENCES cases(id),
    actor_id TEXT REFERENCES users(id), tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL, output_json TEXT NOT NULL,
    success INTEGER NOT NULL, latency_ms INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_cases_invoice ON cases(invoice_code, invoice_number);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_logs(case_id, id);
CREATE INDEX IF NOT EXISTS idx_tool_case ON tool_logs(case_id, id);
"""


def connect(path=None):
    db_path = path or DB_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path=None):
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def new_id():
    return uuid.uuid4().hex


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value):
    return json.loads(value) if value else None


def audit(conn, actor_id, action, case_id=None, from_status=None, to_status=None, detail=None):
    conn.execute("""
        INSERT INTO audit_logs(case_id, actor_id, action, from_status, to_status, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (case_id, actor_id, action, from_status, to_status, dumps(detail or {}), now_iso()))
