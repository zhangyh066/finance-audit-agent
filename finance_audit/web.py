import base64
import binascii
import csv
import io
import json
import sqlite3
from decimal import Decimal
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, tools
from .bootstrap import is_sample_database
from .config import MAX_UPLOAD_BYTES, ROLES, STATIC_DIR
from .db import connect, dumps
from .documents import confirm_document, extract_text, parse_document, store_document
from .policies import import_policy, list_policies, policy_detail, publish_policy
from .security import (FAILURE_WINDOW_SECONDS, RateLimitError, create_user, current_user, list_users, login,
                       logout, session_options, set_user_active)
from .services import (audit_rows, case_detail, create_case, create_cost_center, list_cases, overview, review_case,
                       submit_case, update_budget, update_case)

app = FastAPI(title="衡审｜财务制度与单据智能审核", version=__version__, docs_url=None, redoc_url=None)


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def authenticated(request: Request, conn=Depends(get_db)):
    user = current_user(conn, request.cookies.get("finance_audit_session"))
    if user is None:
        raise HTTPException(401, "请先登录")
    return user


def roles(*allowed):
    def check(user=Depends(authenticated)):
        if user["role"] not in allowed:
            raise PermissionError("当前角色没有此操作权限")
        return user
    return check


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class CaseIn(BaseModel):
    expense_type: str = "住宿费"
    department: str = ""
    cost_center_code: str = ""
    amount: Decimal | None = None
    invoice_date: str = ""
    invoice_code: str = ""
    invoice_number: str = ""
    seller_name: str = ""
    buyer_name: str = ""
    city: str = ""
    description: str = ""
    document_id: str = ""


class ReviewIn(BaseModel):
    action: Literal["approve", "return", "reject"]
    comment: str = Field(default="", max_length=1000)


class DocumentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str
    kind: Literal["invoice", "policy"]


class BudgetIn(BaseModel):
    amount: Decimal


class ToolIn(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class UserIn(BaseModel):
    username: str
    display_name: str
    role: Literal["employee", "finance", "admin", "auditor"]
    password: str = Field(min_length=1, max_length=200)


class ActiveIn(BaseModel):
    active: bool


class CostCenterIn(BaseModel):
    code: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=2, max_length=80)
    amount: Decimal


@app.middleware("http")
async def security_headers(request, call_next):
    if request.url.path.startswith("/api") and request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin.rstrip("/") != expected_origin:
            return JSONResponse({"detail": "请求来源不受信任"}, 403)
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Content-Length 不合法"}, 400)
        limit = MAX_UPLOAD_BYTES * 2 + 65536 if request.url.path == "/api/documents/import" else 128 * 1024
        if content_length > limit:
            return JSONResponse({"detail": "请求体超过大小限制"}, 413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid_value(_, exc):
    return JSONResponse({"detail": str(exc)}, 400)


@app.exception_handler(PermissionError)
async def forbidden(_, exc):
    return JSONResponse({"detail": str(exc)}, 403)


@app.exception_handler(RateLimitError)
async def rate_limited(_, exc):
    return JSONResponse({"detail": str(exc)}, 429, headers={"Retry-After": str(FAILURE_WINDOW_SECONDS)})


@app.exception_handler(LookupError)
async def missing(_, exc):
    return JSONResponse({"detail": str(exc)}, 404)


@app.exception_handler(sqlite3.IntegrityError)
async def conflict(_, __):
    return JSONResponse({"detail": "数据冲突：账号、制度版本或业务记录已存在"}, 409)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "finance-audit-agent", "version": __version__}


@app.post("/api/auth/login")
def sign_in(request: Request, payload: LoginIn, response: Response, conn=Depends(get_db)):
    user, token = login(conn, payload.username, payload.password, request.client.host if request.client else "local")
    response.set_cookie(**session_options(), value=token)
    return {"user": user}


@app.post("/api/auth/logout")
def sign_out(request: Request, response: Response, conn=Depends(get_db)):
    logout(conn, request.cookies.get("finance_audit_session"))
    response.delete_cookie("finance_audit_session", path="/")
    return {"success": True}


@app.get("/api/me")
def me(conn=Depends(get_db), user=Depends(authenticated)):
    sample = is_sample_database(conn)
    return {"user": user, "sample_mode": sample, "demo": sample, "role_name": ROLES[user["role"]]}


@app.get("/api/overview")
def dashboard(conn=Depends(get_db), user=Depends(authenticated)):
    return overview(conn, user)


@app.get("/api/cases")
def cases(status: str = "", q: str = "", conn=Depends(get_db), user=Depends(authenticated)):
    return {"items": list_cases(conn, user, status, q)}


@app.post("/api/cases", status_code=201)
def create(payload: CaseIn, conn=Depends(get_db), user=Depends(roles("employee"))):
    return {"id": create_case(conn, payload.model_dump(), user)}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, conn=Depends(get_db), user=Depends(authenticated)):
    return case_detail(conn, case_id, user)


@app.patch("/api/cases/{case_id}")
def edit_case(case_id: str, payload: CaseIn, conn=Depends(get_db), user=Depends(roles("employee"))):
    update_case(conn, case_id, payload.model_dump(exclude_unset=True), user)
    return {"success": True}


@app.post("/api/cases/{case_id}/submit")
def submit(case_id: str, conn=Depends(get_db), user=Depends(roles("employee"))):
    return {"evaluation_id": submit_case(conn, case_id, user)}


@app.post("/api/cases/{case_id}/review")
def review(case_id: str, payload: ReviewIn, conn=Depends(get_db), user=Depends(roles("finance", "admin"))):
    return {"evaluation_id": review_case(conn, case_id, payload.action, payload.comment, user)}


@app.get("/api/policies")
def policies(conn=Depends(get_db), user=Depends(authenticated)):
    return {"items": list_policies(conn)}


@app.get("/api/policies/{policy_id}")
def policy(policy_id: str, conn=Depends(get_db), user=Depends(authenticated)):
    return policy_detail(conn, policy_id)


@app.post("/api/policies/{policy_id}/publish")
def publish(policy_id: str, conn=Depends(get_db), user=Depends(roles("admin"))):
    publish_policy(conn, policy_id, user["id"])
    return {"success": True}


@app.post("/api/documents/import")
def import_document(payload: DocumentIn, conn=Depends(get_db), user=Depends(authenticated)):
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("文件编码不正确") from exc
    if payload.kind == "policy":
        if user["role"] != "admin":
            raise PermissionError("只有制度管理员可以导入制度")
        text, parser = extract_text(payload.filename, raw)
        policy_id = import_policy(conn, text, payload.filename, user["id"])
        return {"policy": policy_detail(conn, policy_id), "parser": parser}
    if user["role"] != "employee":
        raise PermissionError("请使用报销员工角色导入单据")
    parsed = parse_document(payload.filename, raw, "invoice")
    document_id = store_document(conn, parsed, "invoice", user["id"])
    return {**parsed, "document_id": document_id, "needs_confirmation": "ocr" in parsed["parser"].lower()}


@app.post("/api/documents/{document_id}/confirm")
def confirm(document_id: str, payload: CaseIn, conn=Depends(get_db), user=Depends(roles("employee"))):
    fields = confirm_document(conn, document_id, payload.model_dump(), user)
    return {"fields": fields, "confirmed": True}


@app.put("/api/cost-centers/{code}")
def budget(code: str, payload: BudgetIn, conn=Depends(get_db), user=Depends(roles("admin"))):
    update_budget(conn, code, payload.amount, user)
    return {"success": True}


@app.get("/api/users")
def users(conn=Depends(get_db), user=Depends(roles("admin"))):
    return {"items": list_users(conn)}


@app.post("/api/users", status_code=201)
def create_local_user(payload: UserIn, conn=Depends(get_db), user=Depends(roles("admin"))):
    user_id = create_user(conn, payload.username, payload.display_name, payload.role, payload.password, user["id"])
    return {"id": user_id}


@app.patch("/api/users/{user_id}")
def change_user_active(user_id: str, payload: ActiveIn, conn=Depends(get_db), user=Depends(roles("admin"))):
    set_user_active(conn, user_id, payload.active, user["id"])
    return {"success": True}


@app.post("/api/cost-centers", status_code=201)
def new_cost_center(payload: CostCenterIn, conn=Depends(get_db), user=Depends(roles("admin"))):
    create_cost_center(conn, payload.code, payload.name, payload.amount, user)
    return {"success": True}


@app.get("/api/accounts")
def accounts(conn=Depends(get_db), user=Depends(authenticated)):
    return {"items": [dict(row) for row in conn.execute("SELECT * FROM accounts ORDER BY code")],
            "status": "suggestion_only", "posted": False}


@app.get("/api/audit")
def audit_log(case_id: str = "", conn=Depends(get_db), user=Depends(roles("admin", "auditor"))):
    return {"items": audit_rows(conn, case_id)}


@app.get("/api/tools")
def tool_list(user=Depends(authenticated)):
    return {"items": tools.SPECS, "mcp_command": "python -m finance_audit.mcp_server",
            "mcp_scope": "支持 initialize、tools/list、tools/call；仅本地只读工具，不是完整 MCP 平台"}


@app.post("/api/tools/call")
def call_tool(payload: ToolIn, conn=Depends(get_db), user=Depends(authenticated)):
    return {"result": tools.execute(conn, payload.name, payload.arguments, actor_id=user["id"])}


def _csv(rows, columns):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for label, _ in columns])
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key, "")
            if isinstance(value, (dict, list)):
                value = dumps(value)
            text = str(value if value is not None else "")
            if text.startswith(("=", "+", "-", "@")):
                text = "'" + text
            values.append(text)
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig")


@app.get("/api/export/cases.csv")
def export_cases(conn=Depends(get_db), user=Depends(authenticated)):
    rows = list_cases(conn, user)
    content = _csv(rows, [("单据编号", "case_no"), ("报销人", "owner_name"), ("费用类型", "expense_type"),
                           ("金额（元）", "amount_yuan"), ("状态", "status_label"), ("成本中心", "cost_center_code"),
                           ("制度版本", "policy_version"), ("提交时间", "submitted_at")])
    return Response(content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=cases.csv"})


@app.get("/api/export/audit.csv")
def export_audit(conn=Depends(get_db), user=Depends(roles("admin", "auditor"))):
    rows = audit_rows(conn, limit=10000)
    content = _csv(rows, [("时间", "created_at"), ("单据编号", "case_no"), ("操作人", "actor_name"),
                           ("动作", "action"), ("原状态", "from_status"), ("新状态", "to_status"), ("详情", "detail")])
    return Response(content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=audit.csv"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
