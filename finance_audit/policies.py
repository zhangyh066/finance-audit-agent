import json
import re
from pathlib import Path

from .db import audit, dumps, loads, new_id, now_iso, transaction

ALIASES = {
    "名称": "name", "规则名称": "name", "name": "name",
    "费用类型": "expense_type", "适用费用": "expense_type", "expense_type": "expense_type",
    "检查字段": "field", "字段": "field", "field": "field",
    "操作": "operator", "比较方式": "operator", "operator": "operator",
    "值": "value", "参数": "value", "value": "value",
    "级别": "severity", "严重程度": "severity", "severity": "severity",
    "提示": "message", "错误提示": "message", "message": "message",
}
ALLOWED_FIELDS = {"expense_type", "department", "cost_center_code", "amount_cents", "invoice_date", "invoice_code", "invoice_number", "seller_name", "buyer_name", "city", "description", "document_id"}
ALLOWED_OPERATORS = {"required", "equals", "date_not_after", "unique_invoice", "city_cap", "max_amount", "budget_available", "document_match"}


def _value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def validate_policy(data):
    if not data.get("name") or not data.get("version"):
        raise ValueError("制度名称与版本不能为空")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("制度中至少需要一条规则")
    codes = set()
    for rule in rules:
        code = str(rule.get("code", ""))
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,64}", code):
            raise ValueError(f"规则编码不合法：{code or '空'}")
        if code in codes:
            raise ValueError(f"规则编码重复：{code}")
        codes.add(code)
        if not rule.get("name") or not rule.get("message"):
            raise ValueError(f"{code} 缺少名称或提示")
        if rule.get("field") not in ALLOWED_FIELDS:
            raise ValueError(f"{code} 检查字段不支持")
        if rule.get("operator") not in ALLOWED_OPERATORS:
            raise ValueError(f"{code} 操作符不支持")
        if rule.get("severity") not in {"error", "warning"}:
            raise ValueError(f"{code} 级别只能是 error 或 warning")
        operator, value = rule["operator"], rule.get("value")
        if operator == "required" and value is not True:
            raise ValueError(f"{code} 的 required 参数必须为 true")
        if operator == "equals" and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{code} 的 equals 参数必须为非空文本")
        if operator == "date_not_after" and value not in {"today", "submitted_at"}:
            raise ValueError(f"{code} 只支持 today 或 submitted_at")
        if operator == "max_amount" and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
            raise ValueError(f"{code} 的 max_amount 参数必须为正数")
        if operator == "city_cap" and (not isinstance(value, dict) or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in value.values())):
            raise ValueError(f"{code} 的 city_cap 参数必须为正数限额表")
        if operator in {"unique_invoice", "budget_available"} and value not in (None, {}):
            raise ValueError(f"{code} 不接受额外参数")
        if operator == "document_match":
            allowed = {"invoice_number", "invoice_code", "invoice_date", "buyer_name", "seller_name", "city", "amount"}
            if not isinstance(value, dict) or not isinstance(value.get("fields"), list) or not value["fields"] or not set(value["fields"]) <= allowed:
                raise ValueError(f"{code} 的 fields 必须是支持的导入字段列表")
    return data


def parse_policy(text, source_name="制度.txt"):
    text = text.strip()
    if not text:
        raise ValueError("制度内容为空")
    if text.startswith("{"):
        return validate_policy(json.loads(text))
    data = {"name": Path(source_name).stem, "version": "1.0", "rules": []}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("制度名称：") or line.startswith("制度名称:"):
            data["name"] = line.split("：", 1)[1] if "：" in line else line.split(":", 1)[1]
            continue
        if line.startswith("版本：") or line.startswith("版本:"):
            data["version"] = line.split("：", 1)[1] if "：" in line else line.split(":", 1)[1]
            continue
        match = re.fullmatch(r"\[([A-Z][A-Z0-9_]{2,64})]", line)
        if match:
            current = {"code": match.group(1), "expense_type": "*"}
            data["rules"].append(current)
            continue
        if current is None or not (("=" in line) or ("：" in line) or (":" in line)):
            continue
        separator = "=" if "=" in line else ("：" if "：" in line else ":")
        key, value = line.split(separator, 1)
        key = ALIASES.get(key.strip())
        if key:
            current[key] = _value(value.strip())
    return validate_policy(data)


def import_policy(conn, text, source_name, actor_id):
    data = parse_policy(text, source_name)
    policy_id = new_id()
    with transaction(conn):
        conn.execute("""
            INSERT INTO policies(id, name, version, status, source_name, source_text, created_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?)
        """, (policy_id, data["name"], str(data["version"]), source_name, text, now_iso()))
        for index, rule in enumerate(data["rules"]):
            conn.execute("""
                INSERT INTO rules(id, policy_id, code, name, expense_type, field, operator, value_json, severity, message, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_id(), policy_id, rule["code"], rule["name"], rule["expense_type"], rule["field"],
                  rule["operator"], dumps(rule.get("value")), rule["severity"], rule["message"], index))
        audit(conn, actor_id, "policy.imported", detail={"policy_id": policy_id, "name": data["name"], "version": str(data["version"]), "rule_count": len(data["rules"])})
    return policy_id


def publish_policy(conn, policy_id, actor_id):
    with transaction(conn):
        row = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
        if row is None:
            raise LookupError("制度不存在")
        if row["status"] != "draft":
            raise ValueError("只能发布草稿版本的制度")
        conn.execute("UPDATE policies SET status = 'published', published_at = ? WHERE id = ?", (now_iso(), policy_id))
        audit(conn, actor_id, "policy.published", detail={"policy_id": policy_id, "name": row["name"], "version": row["version"]})


def latest_policy(conn):
    return conn.execute("""
        SELECT * FROM policies WHERE status = 'published'
        ORDER BY published_at DESC, rowid DESC LIMIT 1
    """).fetchone()


def policy_detail(conn, policy_id):
    row = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
    if row is None:
        raise LookupError("制度不存在")
    rules = conn.execute("SELECT * FROM rules WHERE policy_id = ? ORDER BY sort_order", (policy_id,)).fetchall()
    return {**dict(row), "rules": [{**dict(rule), "value": loads(rule["value_json"])} for rule in rules]}


def list_policies(conn):
    current = latest_policy(conn)
    rows = conn.execute("""
        SELECT policies.*, count(rules.id) AS rule_count FROM policies
        LEFT JOIN rules ON rules.policy_id = policies.id
        GROUP BY policies.id ORDER BY policies.created_at DESC, policies.rowid DESC
    """).fetchall()
    return [{**dict(row), "is_current": bool(current and current["id"] == row["id"])} for row in rows]
