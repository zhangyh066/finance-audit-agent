import json
from datetime import date, timedelta

from .config import DEMO_COMPANY, EXAMPLES_DIR
from .db import transaction
from .documents import parse_document, store_document
from .policies import import_policy, publish_policy
from .security import hash_password
from .services import create_case, review_case, submit_case

DEMO_PASSWORD = "demo123"
USERS = [
    ("demo-employee", "employee", "报销员工（演示）", "employee"),
    ("demo-finance", "finance", "财务审核（演示）", "finance"),
    ("demo-admin", "admin", "制度管理员（演示）", "admin"),
    ("demo-auditor", "auditor", "审计查看（演示）", "auditor"),
]
EMPLOYEE = {"id": "demo-employee", "role": "employee"}
FINANCE = {"id": "demo-finance", "role": "finance"}


def seed_demo(conn):
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return False
    with transaction(conn):
        for user_id, username, display_name, role in USERS:
            conn.execute("INSERT INTO users(id, username, display_name, role, password_hash) VALUES (?, ?, ?, ?, ?)",
                         (user_id, username, display_name, role, hash_password(DEMO_PASSWORD)))
        for code, name, budget in (("CC-RD", "研发中心（演示）", 20_000), ("CC-MKT", "市场中心（演示）", 12_000), ("CC-EVENT", "市场活动（演示）", 1_000)):
            conn.execute("INSERT INTO cost_centers(code, name, budget_cents) VALUES (?, ?, ?)", (code, name, budget * 100))
        for code, name, category in (("6602.01", "管理费用—差旅费", "expense"), ("6602.02", "管理费用—交通费", "expense"),
                                     ("6602.03", "管理费用—业务招待费", "expense"), ("6602.04", "管理费用—办公费", "expense"),
                                     ("2241.01", "其他应付款—员工报销", "liability")):
            conn.execute("INSERT INTO accounts(code, name, category) VALUES (?, ?, ?)", (code, name, category))
        conn.execute("INSERT INTO app_meta(key, value) VALUES ('dataset', 'synthetic')")
    policy_id = import_policy(conn, (EXAMPLES_DIR / "travel_policy.txt").read_text(encoding="utf-8"), "travel_policy.txt", "demo-admin")
    publish_policy(conn, policy_id, "demo-admin")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    base = {"department": "研发中心（演示）", "cost_center_code": "CC-RD", "invoice_code": "DEMO", "invoice_date": yesterday,
            "seller_name": "示例酒店（虚构）", "buyer_name": DEMO_COMPANY}
    samples = [
        {"description": "上海住宿费（合成样例）", "city": "上海", "expense_type": "住宿费", "amount": "580.00", "invoice_number": "DEMO000101", "action": "submit"},
        {"description": "北京住宿超额（合成样例）", "city": "北京", "expense_type": "住宿费", "amount": "680.00", "invoice_number": "DEMO000102", "action": "submit"},
        {"description": "杭州交通费（合成样例）", "city": "杭州", "expense_type": "交通费", "amount": "320.00", "invoice_number": "DEMO000103", "action": "approve"},
        {"description": "成都住宿费（合成样例）", "city": "成都", "expense_type": "住宿费", "amount": "360.00", "invoice_number": "DEMO000104", "action": "approve"},
        {"description": "广州住宿超额退回（合成样例）", "city": "广州", "expense_type": "住宿费", "amount": "760.00", "invoice_number": "DEMO000105", "action": "return"},
        {"description": "购买方不一致驳回（合成样例）", "city": "上海", "expense_type": "住宿费", "amount": "230.00", "invoice_number": "DEMO000106", "buyer_name": "错误主体（虚构）", "action": "reject"},
        {"description": "市场活动招待费（合成样例）", "city": "上海", "expense_type": "业务招待费", "amount": "850.00", "invoice_number": "DEMO000107", "cost_center_code": "CC-EVENT", "action": "approve_warning"},
        {"description": "重复发票拦截（合成样例）", "city": "成都", "expense_type": "住宿费", "amount": "360.00", "invoice_number": "DEMO000104", "action": "submit"},
        {"description": "预算不足提示（合成样例）", "city": "上海", "expense_type": "业务招待费", "amount": "300.00", "invoice_number": "DEMO000109", "cost_center_code": "CC-EVENT", "action": "submit"},
        {"description": "尚未提交的草稿（合成样例）", "city": "上海", "expense_type": "住宿费", "amount": "390.00", "invoice_number": "DEMO000110", "action": "draft"},
    ]
    for sample in samples:
        sample = {**base, **sample}
        action = sample.pop("action")
        document = parse_document(f"{sample['invoice_number']}.json", json.dumps(sample, ensure_ascii=False).encode(), "invoice")
        sample["document_id"] = store_document(conn, document, "invoice", "demo-employee")
        case_id = create_case(conn, sample, EMPLOYEE)
        if action == "draft":
            continue
        submit_case(conn, case_id, EMPLOYEE)
        if action == "approve":
            review_case(conn, case_id, "approve", "", FINANCE)
        elif action == "approve_warning":
            review_case(conn, case_id, "approve", "合成演示：人工核对业务必要性后通过", FINANCE)
        elif action == "return":
            review_case(conn, case_id, "return", "合成演示：住宿金额超限，请核对后修改", FINANCE)
        elif action == "reject":
            review_case(conn, case_id, "reject", "合成演示：购买方与报销主体不一致", FINANCE)
    return True
