import secrets
from datetime import datetime

from . import tools
from .config import ROLES
from .db import audit, dumps, loads, new_id, now_iso, transaction
from .money import to_cents, yuan
from .policies import latest_policy, policy_detail
from .rules import save_evaluation

STATUS_LABELS = {"draft": "草稿", "pending_review": "待审核", "returned": "已退回", "approved": "已通过", "rejected": "已驳回"}
CASE_FIELDS = {"expense_type", "department", "cost_center_code", "invoice_date", "invoice_code", "invoice_number", "seller_name", "buyer_name", "city", "description", "document_id"}
LENGTHS = {"expense_type": 30, "department": 80, "cost_center_code": 40, "invoice_date": 10, "invoice_code": 40, "invoice_number": 64, "seller_name": 150, "buyer_name": 150, "city": 50, "description": 2000}


def _case(conn, case_id):
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise LookupError("单据不存在")
    return row


def _access(row, user):
    if user["role"] not in {"finance", "admin", "auditor"} and row["owner_id"] != user["id"]:
        raise PermissionError("只能查看本人单据")


def _payload(conn, data, owner_id):
    result = {key: str(data.get(key, "") or "").strip() for key in CASE_FIELDS}
    for key, maximum in LENGTHS.items():
        if len(result[key]) > maximum:
            raise ValueError(f"{key} 不能超过 {maximum} 字")
    if result["expense_type"] not in {"住宿费", "交通费", "业务招待费", "办公费"}:
        raise ValueError("费用类型不在本项目范围内")
    if result["cost_center_code"] and not conn.execute("SELECT 1 FROM cost_centers WHERE code = ?", (result["cost_center_code"],)).fetchone():
        raise ValueError("成本中心不存在")
    if result["invoice_date"]:
        try:
            datetime.strptime(result["invoice_date"], "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("发票日期格式应为 YYYY-MM-DD") from exc
    if result["document_id"]:
        doc = conn.execute("SELECT uploader_id, kind FROM documents WHERE id = ?", (result["document_id"],)).fetchone()
        if doc is None or doc["kind"] != "invoice":
            raise ValueError("关联的单据文件不存在")
        if doc["uploader_id"] != owner_id:
            raise PermissionError("不能关联他人上传的文件")
    result["amount_cents"] = to_cents(data.get("amount"))
    result["document_id"] = result["document_id"] or None
    result["cost_center_code"] = result["cost_center_code"] or None
    return result


def create_case(conn, data, user):
    if user["role"] != "employee":
        raise PermissionError("请使用报销员工角色创建单据")
    values = _payload(conn, data, user["id"])
    case_id = new_id()
    case_no = f"BX-{datetime.now():%Y%m%d}-{secrets.token_hex(3).upper()}"
    with transaction(conn):
        conn.execute("""
            INSERT INTO cases(id, case_no, owner_id, status, expense_type, department, cost_center_code,
                amount_cents, invoice_date, invoice_code, invoice_number, seller_name, buyer_name, city,
                description, document_id, created_at, updated_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (case_id, case_no, user["id"], values["expense_type"], values["department"], values["cost_center_code"],
              values["amount_cents"], values["invoice_date"], values["invoice_code"], values["invoice_number"],
              values["seller_name"], values["buyer_name"], values["city"], values["description"],
              values["document_id"], now_iso(), now_iso()))
        audit(conn, user["id"], "case.created", case_id, to_status="draft", detail={"case_no": case_no})
    return case_id


def update_case(conn, case_id, data, user):
    row = _case(conn, case_id)
    if row["owner_id"] != user["id"]:
        raise PermissionError("只能修改本人单据")
    if row["status"] not in {"draft", "returned"}:
        raise ValueError("只有草稿或已退回单据可以修改")
    values = _payload(conn, {**dict(row), "amount": yuan(row["amount_cents"]), **data}, user["id"])
    assignments = ", ".join(f"{field} = ?" for field in CASE_FIELDS)
    with transaction(conn):
        conn.execute(f"UPDATE cases SET {assignments}, amount_cents = ?, updated_at = ? WHERE id = ?",
                     [values[field] for field in CASE_FIELDS] + [values["amount_cents"], now_iso(), case_id])
        audit(conn, user["id"], "case.updated", case_id, row["status"], row["status"], {"fields": sorted(CASE_FIELDS | {"amount"})})


def submit_case(conn, case_id, user):
    with transaction(conn):
        row = _case(conn, case_id)
        if row["owner_id"] != user["id"]:
            raise PermissionError("只能提交本人单据")
        if row["status"] not in {"draft", "returned"}:
            raise ValueError("当前状态不能提交")
        policy = latest_policy(conn)
        if policy is None:
            raise ValueError("请先由制度管理员发布制度")
        evaluation_id, results = save_evaluation(conn, row, policy)
        conn.execute("""
            UPDATE cases SET status = 'pending_review', policy_id = ?, submitted_at = ?, updated_at = ? WHERE id = ?
        """, (policy["id"], now_iso(), now_iso(), case_id))
        audit(conn, user["id"], "case.submitted", case_id, row["status"], "pending_review", {
            "evaluation_id": evaluation_id, "policy_id": policy["id"], "policy_version": policy["version"],
            "errors": sum(not r["passed"] and r["severity"] == "error" for r in results),
            "warnings": sum(not r["passed"] and r["severity"] == "warning" for r in results),
        })
    return evaluation_id


def review_case(conn, case_id, action, comment, user):
    if user["role"] not in {"finance", "admin"}:
        raise PermissionError("只有财务审核或制度管理员可以审批")
    if action not in {"approve", "return", "reject"}:
        raise ValueError("审批动作不合法")
    comment = str(comment or "").strip()
    if len(comment) > 1000:
        raise ValueError("审核意见不能超过 1000 字")
    with transaction(conn):
        row = _case(conn, case_id)
        if row["status"] != "pending_review":
            raise ValueError("只有待审核单据可以审批")
        policy = conn.execute("SELECT * FROM policies WHERE id = ?", (row["policy_id"],)).fetchone()
        evaluation_id, results = save_evaluation(conn, row, policy)
        errors = [r for r in results if not r["passed"] and r["severity"] == "error"]
        warnings = [r for r in results if not r["passed"] and r["severity"] == "warning"]
        if action == "approve" and errors:
            raise ValueError("存在未解决的阻断问题，不能通过；请退回补充或驳回")
        if action in {"return", "reject"} and not comment:
            raise ValueError("退回或驳回必须填写原因")
        if action == "approve" and warnings and not comment:
            raise ValueError("有警告事项，人工复核通过必须填写原因")
        new_status = {"approve": "approved", "return": "returned", "reject": "rejected"}[action]
        entry = None
        if action == "approve":
            entry = tools.execute(conn, "account.suggest", {"expense_type": row["expense_type"]}, case_id, user["id"])
        conn.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", (new_status, now_iso(), case_id))
        audit(conn, user["id"], f"case.{action}d" if action == "return" else f"case.{action}d", case_id,
              row["status"], new_status, {"comment": comment, "evaluation_id": evaluation_id,
              "warning_review": bool(warnings), "accounting_status": "suggestion_only" if entry else None})
    return evaluation_id


def list_cases(conn, user, status="", query=""):
    sql = """
        SELECT cases.*, users.display_name AS owner_name, policies.version AS policy_version
        FROM cases JOIN users ON users.id = cases.owner_id
        LEFT JOIN policies ON policies.id = cases.policy_id WHERE 1 = 1
    """
    args = []
    if user["role"] == "employee":
        sql += " AND cases.owner_id = ?"
        args.append(user["id"])
    if status:
        if status not in STATUS_LABELS:
            raise ValueError("状态筛选不合法")
        sql += " AND cases.status = ?"
        args.append(status)
    if query:
        sql += " AND (cases.case_no LIKE ? OR cases.description LIKE ? OR users.display_name LIKE ?)"
        args.extend([f"%{query.strip()[:100]}%"] * 3)
    sql += " ORDER BY cases.updated_at DESC, cases.rowid DESC LIMIT 500"
    rows = conn.execute(sql, args).fetchall()
    return [{**dict(row), "amount_yuan": yuan(row["amount_cents"]), "status_label": STATUS_LABELS[row["status"]]} for row in rows]


def case_detail(conn, case_id, user):
    row = _case(conn, case_id)
    _access(row, user)
    owner = conn.execute("SELECT display_name, username FROM users WHERE id = ?", (row["owner_id"],)).fetchone()
    evaluation = conn.execute("SELECT * FROM evaluations WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (case_id,)).fetchone()
    checks = []
    if evaluation:
        checks = conn.execute("SELECT * FROM rule_results WHERE evaluation_id = ? ORDER BY id", (evaluation["id"],)).fetchall()
    policy = policy_detail(conn, row["policy_id"]) if row["policy_id"] else None
    document = conn.execute("SELECT id, filename, sha256, parser, extracted_text, created_at FROM documents WHERE id = ?", (row["document_id"],)).fetchone() if row["document_id"] else None
    history = conn.execute("""
        SELECT audit_logs.*, users.display_name AS actor_name FROM audit_logs
        LEFT JOIN users ON users.id = audit_logs.actor_id WHERE case_id = ? ORDER BY id DESC
    """, (case_id,)).fetchall()
    tool_history = conn.execute("""
        SELECT tool_logs.*, users.display_name AS actor_name FROM tool_logs
        LEFT JOIN users ON users.id = tool_logs.actor_id WHERE case_id = ? ORDER BY id DESC LIMIT 30
    """, (case_id,)).fetchall()
    entry = tools._account({"expense_type": row["expense_type"]}, conn)
    return {
        **dict(row), "amount_yuan": yuan(row["amount_cents"]), "status_label": STATUS_LABELS[row["status"]],
        "owner_name": owner["display_name"], "policy": policy,
        "evaluation": {**dict(evaluation), "checks": [{**dict(check), "passed": bool(check["passed"]),
            "actual": loads(check["actual_json"]), "expected": loads(check["expected_json"])} for check in checks]} if evaluation else None,
        "document": dict(document) if document else None,
        "history": [{**dict(item), "detail": loads(item["detail_json"])} for item in history],
        "tools": [{**dict(item), "input": loads(item["input_json"]), "output": loads(item["output_json"])} for item in tool_history],
        "accounting_suggestion": {**entry, "amount_yuan": yuan(row["amount_cents"]), "entry_date": row["invoice_date"]},
    }


def overview(conn, user):
    rows = list_cases(conn, user)
    counts = {status: sum(row["status"] == status for row in rows) for status in STATUS_LABELS}
    budgets = conn.execute("SELECT * FROM cost_centers ORDER BY code").fetchall()
    budget_rows = []
    for row in budgets:
        used = conn.execute("SELECT coalesce(sum(amount_cents), 0) FROM cases WHERE cost_center_code = ? AND status = 'approved'", (row["code"],)).fetchone()[0]
        budget_rows.append({"code": row["code"], "name": row["name"], "budget_yuan": yuan(row["budget_cents"]),
                            "used_yuan": yuan(used), "available_yuan": yuan(row["budget_cents"] - used),
                            "usage": used / row["budget_cents"] if row["budget_cents"] else 0})
    return {"total": len(rows), "counts": counts, "approved_amount_yuan": yuan(sum(row["amount_cents"] or 0 for row in rows if row["status"] == "approved")),
            "recent": rows[:8], "budgets": budget_rows, "roles": ROLES}


def update_budget(conn, code, amount, user):
    if user["role"] != "admin":
        raise PermissionError("只有制度管理员可以调整预算")
    cents = to_cents(amount)
    if cents is None or cents < 0:
        raise ValueError("预算必须是非负金额")
    with transaction(conn):
        row = conn.execute("SELECT * FROM cost_centers WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise LookupError("成本中心不存在")
        conn.execute("UPDATE cost_centers SET budget_cents = ? WHERE code = ?", (cents, code))
        audit(conn, user["id"], "budget.updated", detail={"cost_center_code": code, "old_yuan": yuan(row["budget_cents"]), "new_yuan": yuan(cents)})


def create_cost_center(conn, code, name, amount, user):
    if user["role"] != "admin":
        raise PermissionError("只有制度管理员可以新增成本中心")
    code = code.strip().upper()
    name = name.strip()
    if not 3 <= len(code) <= 32 or not code[0].isalpha() or not all(char.isalnum() or char in "_-" for char in code):
        raise ValueError("成本中心编码需以字母开头，仅包含字母、数字、下划线或连字符")
    if not 2 <= len(name) <= 80:
        raise ValueError("成本中心名称应为 2～80 字")
    cents = to_cents(amount)
    if cents is None or cents < 0:
        raise ValueError("预算必须是非负金额")
    with transaction(conn):
        conn.execute("INSERT INTO cost_centers(code, name, budget_cents) VALUES (?, ?, ?)", (code, name, cents))
        audit(conn, user["id"], "cost_center.created", detail={"cost_center_code": code, "name": name, "budget_yuan": yuan(cents)})


def audit_rows(conn, case_id="", limit=500):
    if case_id:
        _case(conn, case_id)
    rows = conn.execute("""
        SELECT audit_logs.*, users.display_name AS actor_name, cases.case_no FROM audit_logs
        LEFT JOIN users ON users.id = audit_logs.actor_id LEFT JOIN cases ON cases.id = audit_logs.case_id
        WHERE (? = '' OR audit_logs.case_id = ?) ORDER BY audit_logs.id DESC LIMIT ?
    """, (case_id, case_id, min(max(limit, 1), 10000))).fetchall()
    return [{**dict(row), "detail": loads(row["detail_json"])} for row in rows]
