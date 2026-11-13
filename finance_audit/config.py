import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "var"
DB_PATH = Path(os.getenv("FINANCE_AUDIT_DB", DATA_DIR / "finance_audit.db"))
STATIC_DIR = BASE_DIR / "static"
EXAMPLES_DIR = BASE_DIR / "examples"
DEMO_COMPANY = "示例科技有限公司（虚构）"
ENGINE_VERSION = "rules-1.0"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
SESSION_COOKIE = "finance_audit_session"
ROLES = {"employee": "报销员工", "finance": "财务审核", "admin": "制度管理员", "auditor": "审计查看"}
