import time
from datetime import date

from .db import dumps, new_id, now_iso

CITY_GROUPS = {
    "一线": {"北京", "上海", "广州", "深圳"},
    "二线": {"杭州", "南京", "苏州", "成都", "武汉", "西安", "重庆", "天津", "青岛", "大连", "厦门", "宁波", "长沙", "郑州", "合肥", "济南", "福州", "无锡", "东莞", "佛山"},
}
DEFAULT_CAPS = {"一线": 600, "二线": 500, "其他": 400}
ACCOUNT_MAP = {
    "住宿费": ("6602.01", "2241.01"),
    "交通费": ("6602.02", "2241.01"),
    "业务招待费": ("6602.03", "2241.01"),
    "办公费": ("6602.04", "2241.01"),
    "差旅费": ("6602.01", "2241.01"),
}
SPECS = [
    {"name": "city.lookup", "description": "查询演示城市分级及当前制度住宿限额", "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}, "policy_id": {"type": "string"}}, "required": ["city"]}},
    {"name": "date.diff", "description": "计算两个 ISO 日期之间的自然日差", "inputSchema": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]}},
    {"name": "budget.status", "description": "查询成本中心预算、已审批占用与可用余额", "inputSchema": {"type": "object", "properties": {"cost_center_code": {"type": "string"}}, "required": ["cost_center_code"]}},
    {"name": "account.suggest", "description": "按费用类型建议费用与应付科目，仅建议不过账", "inputSchema": {"type": "object", "properties": {"expense_type": {"type": "string"}}, "required": ["expense_type"]}},
    {"name": "invoice.duplicate_check", "description": "检查有效流程中的发票代码与号码组合是否重复", "inputSchema": {"type": "object", "properties": {"invoice_code": {"type": "string"}, "invoice_number": {"type": "string"}, "exclude_case_id": {"type": "string"}}, "required": ["invoice_number"]}},
]


def _city(args, conn):
    city = str(args.get("city", "")).strip().removesuffix("市")
    if not city:
        raise ValueError("城市不能为空")
    tier = next((name for name, cities in CITY_GROUPS.items() if city in cities), "其他")
    caps = DEFAULT_CAPS
    policy_id = args.get("policy_id")
    if policy_id:
        row = conn.execute(
            "SELECT value_json FROM rules WHERE policy_id = ? AND code = 'HOTEL_CITY_CAP'", (policy_id,)
        ).fetchone()
        if row:
            import json
            caps = json.loads(row["value_json"])
    return {"city": city, "tier": tier, "hotel_cap_yuan": caps.get(tier), "source": "本地演示城市表与指定制度版本"}


def _date_diff(args, conn):
    start = date.fromisoformat(str(args["start"]))
    end = date.fromisoformat(str(args["end"]))
    return {"start": start.isoformat(), "end": end.isoformat(), "calendar_days": (end - start).days}


def _budget(args, conn):
    code = str(args.get("cost_center_code", "")).strip()
    row = conn.execute("SELECT * FROM cost_centers WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise ValueError("成本中心不存在")
    used = conn.execute(
        "SELECT coalesce(sum(amount_cents), 0) FROM cases WHERE cost_center_code = ? AND status = 'approved'", (code,)
    ).fetchone()[0]
    return {"cost_center_code": code, "name": row["name"], "budget_yuan": row["budget_cents"] / 100,
            "approved_used_yuan": used / 100, "available_yuan": (row["budget_cents"] - used) / 100,
            "definition": "仅已审批金额占用预算，待审金额不预占"}


def _account(args, conn):
    expense_type = str(args.get("expense_type", "")).strip()
    debit, credit = ACCOUNT_MAP.get(expense_type, ACCOUNT_MAP["办公费"])
    entries = []
    for direction, code in (("debit", debit), ("credit", credit)):
        row = conn.execute("SELECT code, name, category FROM accounts WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise ValueError(f"科目未配置：{code}")
        entries.append({"direction": direction, **dict(row)})
    return {"expense_type": expense_type, "entries": entries, "status": "suggestion_only", "posted": False}


def _duplicate(args, conn):
    number = str(args.get("invoice_number", "")).strip()
    code = str(args.get("invoice_code", "")).strip()
    if not number:
        raise ValueError("发票号码不能为空")
    rows = conn.execute("""
        SELECT case_no FROM cases WHERE invoice_code = ? AND invoice_number = ?
          AND status IN ('pending_review', 'approved') AND id != coalesce(?, '')
    """, (code, number, args.get("exclude_case_id"))).fetchall()
    return {"duplicate": bool(rows), "case_numbers": [r["case_no"] for r in rows], "scope": "待审核与已通过单据"}


HANDLERS = {"city.lookup": _city, "date.diff": _date_diff, "budget.status": _budget, "account.suggest": _account, "invoice.duplicate_check": _duplicate}


def execute(conn, name, args, case_id=None, actor_id=None):
    if name not in HANDLERS:
        raise ValueError("不支持的工具")
    started = time.perf_counter()
    success = True
    try:
        result = HANDLERS[name](args or {}, conn)
        return result
    except (KeyError, TypeError) as exc:
        success = False
        result = {"error": "工具参数不完整或类型错误"}
        raise ValueError(result["error"]) from exc
    except Exception as exc:
        success = False
        result = {"error": str(exc)}
        raise
    finally:
        conn.execute("""
            INSERT INTO tool_logs(case_id, actor_id, tool_name, input_json, output_json, success, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (case_id, actor_id, name, dumps(args or {}), dumps(result), int(success),
              round((time.perf_counter() - started) * 1000), now_iso()))
