from datetime import date, datetime
from decimal import Decimal

from . import tools
from .config import ENGINE_VERSION
from .db import dumps, loads, new_id, now_iso
from .money import to_cents, yuan


def _actual(case, field):
    value = case[field]
    return yuan(value) if field == "amount_cents" else value


def _check(case, rule, conn):
    field, operator = rule["field"], rule["operator"]
    value = loads(rule["value_json"])
    actual = _actual(case, field)
    expected = value
    if operator == "required":
        passed = case[field] is not None and str(case[field]).strip() != ""
        if field == "amount_cents":
            passed = case[field] is not None and case[field] > 0
        return passed, actual, "必填且有效"
    if operator == "equals":
        return str(case[field]).strip() == value.strip(), actual, value
    if operator == "max_amount":
        limit = to_cents(value)
        return case["amount_cents"] is not None and case["amount_cents"] <= limit, actual, {"max_yuan": yuan(limit)}
    if operator == "date_not_after":
        try:
            invoice_date = date.fromisoformat(case["invoice_date"])
            if value == "today":
                upper = date.today()
            elif case["submitted_at"]:
                upper = datetime.fromisoformat(case["submitted_at"]).date()
            else:
                upper = date.today()
            return invoice_date <= upper, actual, {"not_after": upper.isoformat()}
        except (ValueError, TypeError):
            return False, actual, "有效 ISO 日期，且不晚于 " + ("今天" if value == "today" else "提交日")
    if operator == "city_cap":
        if case["amount_cents"] is None:
            return False, actual, "先填写有效金额"
        lookup = tools.execute(conn, "city.lookup", {"city": case["city"], "policy_id": rule["policy_id"]}, case["id"])
        cap = lookup.get("hotel_cap_yuan")
        if cap is None:
            return False, {"city": case["city"], "amount_yuan": actual}, "该城市等级未配置住宿限额"
        passed = case["amount_cents"] <= to_cents(cap)
        return passed, {"city": lookup["city"], "tier": lookup["tier"], "amount_yuan": actual}, {"max_yuan": cap}
    if operator == "document_match":
        document = conn.execute("SELECT fields_json FROM documents WHERE id = ?", (case["document_id"],)).fetchone()
        if document is None:
            return False, actual, "导入并关联可核验的单据文件"
        imported = loads(document["fields_json"])
        mismatches = []
        for imported_field in value.get("fields", []):
            case_field = "amount_cents" if imported_field == "amount" else imported_field
            left, right = case[case_field], imported.get(imported_field)
            if imported_field == "amount":
                try:
                    right = to_cents(right)
                except ValueError:
                    right = None
            if left != right:
                mismatches.append({"field": imported_field, "entered": _actual(case, case_field), "imported": imported.get(imported_field)})
        return not mismatches, {"mismatches": mismatches}, value
    if operator == "unique_invoice":
        result = tools.execute(conn, "invoice.duplicate_check", {
            "invoice_code": case["invoice_code"], "invoice_number": case["invoice_number"], "exclude_case_id": case["id"],
        }, case["id"])
        return not result["duplicate"], {"invoice_code": case["invoice_code"], "invoice_number": case["invoice_number"]}, result
    if operator == "budget_available":
        result = tools.execute(conn, "budget.status", {"cost_center_code": case["cost_center_code"]}, case["id"])
        passed = case["amount_cents"] is not None and case["amount_cents"] <= round(Decimal(str(result["available_yuan"])) * 100)
        return passed, actual, result
    raise ValueError("无法执行该规则")


def evaluate(conn, case, policy):
    rules = conn.execute("SELECT * FROM rules WHERE policy_id = ? ORDER BY sort_order", (policy["id"],)).fetchall()
    results = []
    for rule in rules:
        if rule["expense_type"] not in ("*", case["expense_type"]):
            continue
        try:
            passed, actual, expected = _check(case, rule, conn)
            message = "通过" if passed else rule["message"]
        except Exception as exc:
            passed, actual, expected = False, _actual(case, rule["field"]), {"configuration_or_data_error": True}
            message = f"检查未完成：{exc}"
        results.append({
            "rule_code": rule["code"], "rule_name": rule["name"], "severity": rule["severity"],
            "passed": bool(passed), "actual": actual, "expected": expected, "message": message,
        })
    return results


def save_evaluation(conn, case, policy):
    results = evaluate(conn, case, policy)
    evaluation_id = new_id()
    conn.execute("""
        INSERT INTO evaluations(id, case_id, policy_id, engine_version, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (evaluation_id, case["id"], policy["id"], ENGINE_VERSION, now_iso()))
    for result in results:
        conn.execute("""
            INSERT INTO rule_results(evaluation_id, rule_code, rule_name, severity, passed, actual_json, expected_json, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (evaluation_id, result["rule_code"], result["rule_name"], result["severity"], int(result["passed"]),
              dumps(result["actual"]), dumps(result["expected"]), result["message"]))
    return evaluation_id, results
