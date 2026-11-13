from .db import audit, new_id, transaction
from .security import hash_password, validate_password, validate_username

CORE_ACCOUNTS = (
    ("6602.01", "管理费用—差旅费", "expense"),
    ("6602.02", "管理费用—交通费", "expense"),
    ("6602.03", "管理费用—业务招待费", "expense"),
    ("6602.04", "管理费用—办公费", "expense"),
    ("2241.01", "其他应付款—员工报销", "liability"),
)


def initialize_workspace(conn, username, password):
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        raise ValueError("当前数据库已有账号，无需重复初始化")
    validate_username(username)
    validate_password(password)
    with transaction(conn):
        for account in CORE_ACCOUNTS:
            conn.execute("INSERT INTO accounts(code, name, category) VALUES (?, ?, ?)", account)
        conn.execute("INSERT INTO cost_centers(code, name, budget_cents) VALUES ('GENERAL', '通用费用', 0)")
        user_id = new_id()
        conn.execute("INSERT INTO users(id, username, display_name, role, password_hash) VALUES (?, ?, '系统管理员', 'admin', ?)",
                     (user_id, username, hash_password(password)))
        conn.execute("INSERT INTO app_meta(key, value) VALUES ('dataset', 'local')")
        audit(conn, user_id, "workspace.initialized", detail={"username": username, "sample_data": False})


def is_sample_database(conn):
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'dataset'").fetchone()
    if row:
        return row["value"] == "synthetic"
    return bool(conn.execute("SELECT 1 FROM users WHERE id LIKE 'demo-%' LIMIT 1").fetchone())
