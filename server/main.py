# ============================================================================
# ZZyProj 平台 - FastAPI 主入口
# 独立服务：账号 / 会话 / DeepSeek 流式代理 / 配额 / 管理后台
# 约定：所有非 2xx 使用 {code,message,details,request_id}；秘密只在服务端。
# ============================================================================

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import (
    code_matches,
    create_token,
    hash_code,
    hash_password,
    normalize_email,
    validate_email,
    validate_password,
    verify_password,
    verify_token,
)
from config import Config
from db import (
    TZ,
    get_db,
    init_db,
    month_str,
    next_reset_iso,
    now_ts,
    today_str,
    transaction,
    write_lock,
)
from mailer import MailError, MailNotConfigured, send_verification_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("zzy")

# 未命中用户时的等时密码校验，避免“邮箱是否存在”被时间差探测
_DUMMY_HASH = hash_password("__zzy_dummy_password__")


# ============================================================================
# 异常与错误信封
# ============================================================================

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_body(request: Request, code: str, message: str, details: dict | None = None) -> dict:
    rid = getattr(request.state, "request_id", "")
    return {"code": code, "message": message, "details": details or {}, "request_id": rid}


def error_response(request: Request, status: int, code: str, message: str,
                   details: dict | None = None) -> JSONResponse:
    return JSONResponse(error_body(request, code, message, details), status_code=status)


# ============================================================================
# 应用生命周期
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    errors = Config.validate()
    if errors:
        for e in errors:
            logger.error("配置错误: %s", e)
        raise RuntimeError("配置校验失败，拒绝启动：" + "; ".join(errors))

    init_db()
    logger.info("生效配置: %s", json.dumps(Config.masked_summary(), ensure_ascii=False))
    if not Config.smtp_configured():
        logger.warning("SMTP 未配置：注册接口将返回 503（fail closed）")
    if not Config.ADMIN_EMAILS:
        logger.warning("ZZY_ADMIN_EMAILS 为空：没有任何管理员账号")
    else:
        logger.info("管理员邮箱: %s", ", ".join(sorted(Config.ADMIN_EMAILS)))
    yield


app = FastAPI(title=Config.APP_NAME, version=Config.VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)


class NoCacheStatic(StaticFiles):
    """静态资源不缓存：发版后用户刷新即拿到新 JS。"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.mount("/static", NoCacheStatic(directory=Config.SITE_DIR), name="static")


# ============================================================================
# 中间件：request id + 安全头 + 兜底
# ============================================================================

@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or f"zzy-{uuid.uuid4().hex[:12]}"
    request.state.request_id = rid
    started = time.time()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("未处理异常 rid=%s path=%s", rid, request.url.path)
        response = error_response(
            request, 500, "INTERNAL",
            "服务暂时不可用，请稍后重试；若持续失败请把 request_id 发给管理员。",
        )
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    elapsed = int((time.time() - started) * 1000)
    logger.info("rid=%s %s %s %s %dms", rid, request.method, request.url.path,
                response.status_code, elapsed)
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return error_response(request, exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    fields = []
    for item in exc.errors():
        loc = ".".join(str(x) for x in item.get("loc", ()) if x != "body")
        fields.append({"field": loc, "message": item.get("msg", "")})
    readable = "；".join(f"{f['field']}: {f['message']}" for f in fields) or "请求参数不合法"
    return error_response(request, 422, "INVALID_PARAMS", "请求参数不合法：" + readable,
                          {"fields": fields})


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return error_response(request, 404, "NOT_FOUND", "请求的资源不存在")


# ============================================================================
# 限流（进程内滑窗；单进程 uvicorn）
# ============================================================================

class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = {}

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(q[0] + window - now))
            return False, retry
        q.append(now)
        return True, 0


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("X-Real-IP", "")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


# ============================================================================
# 身份依赖
# ============================================================================

def get_current_user(request: Request, authorization: str = Header(None)) -> sqlite3.Row:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = verify_token(token)
    if not payload or not payload.get("sub"):
        raise ApiError(401, "UNAUTHORIZED", "登录状态已失效，请重新登录")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (payload["sub"],)).fetchone()
    if row is None:
        raise ApiError(401, "UNAUTHORIZED", "登录状态已失效，请重新登录")
    if row["disabled"]:
        raise ApiError(403, "ACCOUNT_DISABLED", "账号已被禁用，请联系管理员")
    return row


def require_admin(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
    if user["role"] != "admin":
        raise ApiError(403, "FORBIDDEN", "需要管理员权限")
    return user


# ============================================================================
# 序列化与配额
# ============================================================================

def iso(ts: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(ts, TZ).isoformat() if ts else None


def effective_limits(user: sqlite3.Row) -> tuple[int, int]:
    daily = user["daily_limit"] if user["daily_limit"] is not None else Config.DAILY_MESSAGE_LIMIT
    monthly = (user["monthly_token_limit"] if user["monthly_token_limit"] is not None
               else Config.MONTHLY_TOKEN_LIMIT)
    return int(daily), int(monthly)


def usage_snapshot(conn: sqlite3.Connection, user: sqlite3.Row) -> dict:
    day, month = today_str(), month_str()
    daily_limit, monthly_limit = effective_limits(user)
    drow = conn.execute(
        "SELECT messages, input_tokens, output_tokens FROM usage_daily WHERE user_id=? AND day=?",
        (user["id"], day),
    ).fetchone()
    mrow = conn.execute(
        "SELECT COALESCE(SUM(messages),0) AS messages, "
        "COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens "
        "FROM usage_daily WHERE user_id=? AND day LIKE ?",
        (user["id"], month + "-%"),
    ).fetchone()
    used_today = drow["messages"] if drow else 0
    tokens_month = (mrow["input_tokens"] + mrow["output_tokens"]) if mrow else 0
    return {
        "day": day,
        "daily": {
            "used": used_today,
            "limit": daily_limit,
            "remaining": None if daily_limit < 0 else max(0, daily_limit - used_today),
            "unlimited": daily_limit < 0,
            "resets_at": next_reset_iso("day"),
        },
        "month": month,
        "monthly": {
            "tokens_used": tokens_month,
            "token_limit": monthly_limit,
            "remaining": None if monthly_limit < 0 else max(0, monthly_limit - tokens_month),
            "unlimited": monthly_limit < 0,
            "resets_at": next_reset_iso("month"),
        },
    }


def public_user(user: sqlite3.Row, usage: dict | None = None) -> dict:
    data = {
        "id": user["id"],
        "email": user["email"],
        "nickname": user["nickname"],
        "role": user["role"],
        "disabled": bool(user["disabled"]),
        "created_at": iso(user["created_at"]),
        "last_login": iso(user["last_login"]),
        "limits": {
            "daily_messages": user["daily_limit"],
            "monthly_tokens": user["monthly_token_limit"],
            "daily_messages_default": Config.DAILY_MESSAGE_LIMIT,
            "monthly_tokens_default": Config.MONTHLY_TOKEN_LIMIT,
        },
    }
    if usage is not None:
        data["usage"] = usage
    return data


def reserve_quota(conn: sqlite3.Connection, user: sqlite3.Row) -> None:
    """日次数原子占位 + 月度 token 预检。失败抛 429。"""
    daily_limit, monthly_limit = effective_limits(user)
    day, month = today_str(), month_str()
    with write_lock():
        conn.execute("BEGIN IMMEDIATE")
        try:
            drow = conn.execute(
                "SELECT messages FROM usage_daily WHERE user_id=? AND day=?", (user["id"], day)
            ).fetchone()
            used = drow["messages"] if drow else 0
            if daily_limit >= 0 and used >= daily_limit:
                raise ApiError(
                    429, "QUOTA_DAILY",
                    f"今日额度已用完（{used}/{daily_limit} 条），明日 00:00 重置。",
                    {"used": used, "limit": daily_limit, "resets_at": next_reset_iso("day")},
                )
            mrow = conn.execute(
                "SELECT COALESCE(SUM(input_tokens+output_tokens),0) AS tokens "
                "FROM usage_daily WHERE user_id=? AND day LIKE ?",
                (user["id"], month + "-%"),
            ).fetchone()
            used_tokens = mrow["tokens"] if mrow else 0
            if monthly_limit >= 0 and used_tokens >= monthly_limit:
                raise ApiError(
                    429, "QUOTA_MONTHLY",
                    f"本月 token 额度已用完（{used_tokens}/{monthly_limit}），"
                    f"{next_reset_iso('month')[:10]} 重置。",
                    {"used": used_tokens, "limit": monthly_limit,
                     "resets_at": next_reset_iso("month")},
                )
            conn.execute(
                "INSERT INTO usage_daily(user_id, day, messages) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, day) DO UPDATE SET messages = messages + 1",
                (user["id"], day),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def refund_message_slot(conn: sqlite3.Connection, user_id: str, day: str) -> None:
    with write_lock():
        conn.execute(
            "UPDATE usage_daily SET messages = MAX(messages - 1, 0) WHERE user_id=? AND day=?",
            (user_id, day),
        )


def add_token_usage(conn: sqlite3.Connection, user_id: str, day: str,
                    input_tokens: int, output_tokens: int) -> None:
    with write_lock():
        conn.execute(
            "INSERT INTO usage_daily(user_id, day, messages, input_tokens, output_tokens) "
            "VALUES (?, ?, 0, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET "
            "input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens",
            (user_id, day, int(input_tokens), int(output_tokens)),
        )


def audit(conn: sqlite3.Connection, admin_id: str, target_id: str, action: str,
          details: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO admin_audit(admin_id, target_id, action, details, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (admin_id, target_id, action, json.dumps(details or {}, ensure_ascii=False), now_ts()),
    )


# ============================================================================
# 请求模型
# ============================================================================

class RequestCodeBody(BaseModel):
    email: str
    password: str
    nickname: str = ""


class RegisterBody(BaseModel):
    email: str
    code: str
    nickname: str = ""
    password: str = ""


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


class ChatBody(BaseModel):
    conversation_id: Optional[str] = None
    message: str = ""
    mode: str = "ask"
    model: str = Config.DEFAULT_MODEL
    regenerate: bool = False


class ConversationPatch(BaseModel):
    title: str


class AdminUserPatch(BaseModel):
    disabled: Optional[bool] = None
    daily_limit: Optional[int] = None
    monthly_token_limit: Optional[int] = None
    role: Optional[str] = None


# ============================================================================
# 静态页
# ============================================================================

def _index_response() -> FileResponse:
    return FileResponse(
        os.path.join(Config.SITE_DIR, "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/")
async def root():
    return _index_response()


@app.get("/admin")
async def admin_page():
    return _index_response()


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": Config.VERSION}


# ============================================================================
# 认证
# ============================================================================

@app.post("/api/auth/request-code")
async def request_code(body: RequestCodeBody, request: Request):
    email = normalize_email(body.email)
    if not validate_email(email):
        raise ApiError(400, "INVALID_EMAIL", "邮箱格式不正确")
    pwd_err = validate_password(body.password)
    if pwd_err:
        raise ApiError(400, "INVALID_PASSWORD", pwd_err)
    nickname = (body.nickname or "").strip()
    if len(nickname) > 30:
        raise ApiError(400, "INVALID_NICKNAME", "昵称最多 30 个字符")

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        raise ApiError(409, "EMAIL_EXISTS", "该邮箱已注册，请直接登录")

    if not Config.smtp_configured():
        raise ApiError(503, "MAIL_NOT_CONFIGURED",
                       "邮件服务未配置，请联系管理员（SMTP 未配置）。")

    ip = client_ip(request)
    ip_key = f"code_ip:{ip}:{today_str()}"
    ok, retry = limiter.hit(ip_key, Config.CODE_IP_DAILY, 86400)
    if not ok:
        raise ApiError(429, "RATE_LIMIT_IP",
                       f"该 IP 今日发送验证码次数已达上限（{Config.CODE_IP_DAILY} 次）。",
                       {"retry_after": retry})
    ok, retry = limiter.hit(f"code_email:{email}", 1, Config.CODE_RESEND_SECONDS)
    if not ok:
        raise ApiError(429, "RATE_LIMIT_EMAIL",
                       f"发送太频繁，请 {retry} 秒后再试。", {"retry_after": retry})

    code = f"{int.from_bytes(os.urandom(4), 'big') % 1000000:06d}"
    now = now_ts()
    with get_db() as conn:
        with transaction(conn):
            conn.execute("DELETE FROM email_codes WHERE email=? AND purpose='register'", (email,))
            conn.execute(
                "INSERT INTO email_codes(email, code_hash, password_hash, nickname, purpose, "
                "expires_at, attempts, created_at) VALUES (?, ?, ?, ?, 'register', ?, 0, ?)",
                (email, hash_code(email, code), hash_password(body.password),
                 nickname, now + Config.CODE_TTL_SECONDS, now),
            )

    try:
        await asyncio.to_thread(send_verification_code, email, code)
    except MailNotConfigured:
        with get_db() as conn:
            conn.execute("DELETE FROM email_codes WHERE email=? AND purpose='register'", (email,))
        raise ApiError(503, "MAIL_NOT_CONFIGURED", "邮件服务未配置，请联系管理员。")
    except MailError as exc:
        logger.error("发送验证码失败 rid=%s err=%s",
                     getattr(request.state, "request_id", ""), exc)
        with get_db() as conn:
            conn.execute("DELETE FROM email_codes WHERE email=? AND purpose='register'", (email,))
        raise ApiError(502, "MAIL_SEND_FAILED",
                       "验证码邮件发送失败，请稍后重试；若持续失败请联系管理员。",
                       {"hint": "检查 SMTP 授权码是否过期"})

    logger.info("验证码已发送 email=%s ip=%s", email, ip)
    return {"success": True, "expires_in": Config.CODE_TTL_SECONDS}


@app.post("/api/auth/register")
async def register(body: RegisterBody, request: Request):
    email = normalize_email(body.email)
    if not validate_email(email):
        raise ApiError(400, "INVALID_EMAIL", "邮箱格式不正确")
    code = (body.code or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        raise ApiError(400, "INVALID_CODE", "验证码应为 6 位数字")
    nickname = (body.nickname or "").strip()
    if len(nickname) > 30:
        raise ApiError(400, "INVALID_NICKNAME", "昵称最多 30 个字符")

    now = now_ts()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM email_codes WHERE email=? AND purpose='register' "
            "ORDER BY created_at DESC LIMIT 1", (email,)
        ).fetchone()
        if row is None:
            raise ApiError(400, "CODE_NOT_FOUND", "请先获取验证码")
        if row["expires_at"] < now:
            conn.execute("DELETE FROM email_codes WHERE id=?", (row["id"],))
            raise ApiError(400, "CODE_EXPIRED", "验证码已过期，请重新获取")
        if row["attempts"] >= Config.CODE_MAX_ATTEMPTS:
            raise ApiError(429, "CODE_ATTEMPTS", "验证码尝试次数过多，请重新获取")

        # 失败计数必须立即提交（不能随异常回滚），否则错码次数永远不涨
        if not code_matches(email, code, row["code_hash"]):
            conn.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE id=?",
                         (row["id"],))
            left = max(Config.CODE_MAX_ATTEMPTS - row["attempts"] - 1, 0)
            raise ApiError(400, "CODE_MISMATCH",
                           f"验证码不正确，还可尝试 {left} 次",
                           {"attempts_left": left})

        # 注册时若再次提交密码，以注册请求为准；否则沿用获取验证码时的哈希
        if body.password:
            pwd_err = validate_password(body.password)
            if pwd_err:
                raise ApiError(400, "INVALID_PASSWORD", pwd_err)
            password_hash = hash_password(body.password)
        else:
            password_hash = row["password_hash"]
        nickname = (row["nickname"] or "").strip() or nickname or email.split("@")[0]
        user_id = uuid.uuid4().hex
        role = "admin" if email in Config.ADMIN_EMAILS else "user"
        with transaction(conn):
            existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                conn.execute("DELETE FROM email_codes WHERE email=?", (email,))
                raise ApiError(409, "EMAIL_EXISTS", "该邮箱已注册，请直接登录")
            conn.execute(
                "INSERT INTO users(id, email, password_hash, nickname, role, disabled, "
                "email_verified, created_at, last_login) VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)",
                (user_id, email, password_hash, nickname, role, now, now),
            )
            conn.execute("DELETE FROM email_codes WHERE email=?", (email,))
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        usage = usage_snapshot(conn, user)

    token = create_token(user_id)
    logger.info("注册成功 uid=%s role=%s rid=%s", user_id, role,
                getattr(request.state, "request_id", ""))
    return {"success": True, "token": token, "user": public_user(user, usage)}


# ============================================================================
# 登录 / 当前用户
# ============================================================================

@app.post("/api/auth/login")
async def login(body: LoginBody, request: Request):
    email = normalize_email(body.email)
    ip = client_ip(request)
    ok, retry = limiter.hit(f"login_ip:{ip}", Config.LOGIN_ATTEMPTS_PER_MINUTE, 60)
    if not ok:
        raise ApiError(429, "RATE_LIMIT_LOGIN",
                       f"登录尝试过于频繁，请 {retry} 秒后再试。", {"retry_after": retry})
    ok, retry = limiter.hit(f"login_email:{email}", Config.LOGIN_ATTEMPTS_PER_MINUTE, 60)
    if not ok:
        raise ApiError(429, "RATE_LIMIT_LOGIN",
                       f"该邮箱登录尝试过于频繁，请 {retry} 秒后再试。", {"retry_after": retry})

    if not validate_email(email):
        raise ApiError(400, "INVALID_EMAIL", "邮箱格式不正确")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

    # 不存在时也做一次 bcrypt 校验，降低时间差泄露
    stored = user["password_hash"] if user else _DUMMY_HASH
    password_ok = verify_password(body.password or "", stored)
    if user is None or not password_ok:
        raise ApiError(401, "LOGIN_FAILED", "邮箱或密码不正确")
    if user["disabled"]:
        raise ApiError(403, "ACCOUNT_DISABLED", "账号已被禁用，请联系管理员")

    with get_db() as conn:
        with transaction(conn):
            conn.execute("UPDATE users SET last_login=? WHERE id=?", (now_ts(), user["id"]))
            user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            usage = usage_snapshot(conn, user)

    token = create_token(user["id"])
    logger.info("登录成功 uid=%s rid=%s", user["id"], getattr(request.state, "request_id", ""))
    return {"success": True, "token": token, "user": public_user(user, usage)}


@app.get("/api/auth/me")
async def me(user: sqlite3.Row = Depends(get_current_user)):
    with get_db() as conn:
        fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        usage = usage_snapshot(conn, fresh)
    return {"user": public_user(fresh, usage)}


@app.post("/api/auth/change-password")
async def change_password(body: ChangePasswordBody, request: Request,
                          user: sqlite3.Row = Depends(get_current_user)):
    ok, retry = limiter.hit(f"pwd:{user['id']}", 5, 60)
    if not ok:
        raise ApiError(429, "RATE_LIMIT_PASSWORD",
                       f"操作过于频繁，请 {retry} 秒后再试。", {"retry_after": retry})
    if not verify_password(body.old_password or "", user["password_hash"]):
        raise ApiError(400, "WRONG_PASSWORD", "当前密码不正确")
    err = validate_password(body.new_password)
    if err:
        raise ApiError(400, "INVALID_PASSWORD", err)
    if verify_password(body.new_password, user["password_hash"]):
        raise ApiError(400, "SAME_PASSWORD", "新密码不能与当前密码相同")
    with get_db() as conn:
        with transaction(conn):
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (hash_password(body.new_password), user["id"]))
    logger.info("修改密码 uid=%s rid=%s", user["id"], getattr(request.state, "request_id", ""))
    return {"success": True}


@app.post("/api/auth/logout")
async def logout():
    # JWT 无状态：客户端删除 token 即退出；保留端点便于前端统一调用
    return {"success": True}


# ============================================================================
# 会话
# ============================================================================

@app.get("/api/conversations")
async def list_conversations(user: sqlite3.Row = Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conv_id=c.id) AS message_count "
            "FROM conversations c WHERE c.user_id=? ORDER BY c.updated_at DESC",
            (user["id"],),
        ).fetchall()
    return {"conversations": [
        {"id": r["id"], "title": r["title"], "created_at": iso(r["created_at"]),
         "updated_at": iso(r["updated_at"]), "message_count": r["message_count"]}
        for r in rows
    ]}


@app.post("/api/conversations")
async def create_conversation(user: sqlite3.Row = Depends(get_current_user)):
    conv_id = uuid.uuid4().hex
    now = now_ts()
    with get_db() as conn:
        with transaction(conn):
            conn.execute(
                "INSERT INTO conversations(id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, '新的对话', ?, ?)", (conv_id, user["id"], now, now)
            )
    return {"conversation": {"id": conv_id, "title": "新的对话",
                             "created_at": iso(now), "updated_at": iso(now),
                             "message_count": 0}}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: sqlite3.Row = Depends(get_current_user)):
    with get_db() as conn:
        conv = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user["id"])
        ).fetchone()
        if conv is None:
            raise ApiError(404, "NOT_FOUND", "会话不存在")
        msgs = conn.execute(
            "SELECT id, role, content, reasoning, model, input_tokens, output_tokens, created_at "
            "FROM messages WHERE conv_id=? AND user_id=? ORDER BY rowid ASC",
            (conv_id, user["id"]),
        ).fetchall()
    return {
        "conversation": {"id": conv["id"], "title": conv["title"],
                         "created_at": iso(conv["created_at"]),
                         "updated_at": iso(conv["updated_at"])},
        "messages": [
            {"id": m["id"], "role": m["role"], "content": m["content"],
             "reasoning": m["reasoning"], "model": m["model"],
             "usage": {"input": m["input_tokens"], "output": m["output_tokens"]},
             "created_at": iso(m["created_at"])}
            for m in msgs
        ],
    }


@app.patch("/api/conversations/{conv_id}")
async def patch_conversation(conv_id: str, body: ConversationPatch,
                             user: sqlite3.Row = Depends(get_current_user)):
    title = (body.title or "").strip()
    if not title or len(title) > 60:
        raise ApiError(400, "INVALID_TITLE", "标题长度应为 1–60 个字符")
    with get_db() as conn:
        with transaction(conn):
            cur = conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=? AND user_id=?",
                (title, now_ts(), conv_id, user["id"]),
            )
            if cur.rowcount == 0:
                raise ApiError(404, "NOT_FOUND", "会话不存在")
    return {"success": True, "title": title}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: sqlite3.Row = Depends(get_current_user)):
    with get_db() as conn:
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, user["id"])
            )
            if cur.rowcount == 0:
                raise ApiError(404, "NOT_FOUND", "会话不存在")
    return {"success": True}


# ============================================================================
# 聊天（SSE）
# ============================================================================

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _upstream_error_message(status: int) -> tuple[str, str]:
    if status == 401:
        return "UPSTREAM_AUTH", "模型服务密钥无效，请联系管理员。"
    if status == 402:
        return "UPSTREAM_BALANCE", "模型账户余额不足，请联系管理员。"
    if status == 429:
        return "UPSTREAM_RATE_LIMIT", "模型请求过于频繁，请稍后重试。"
    if status >= 500:
        return "UPSTREAM_UNAVAILABLE", "模型服务暂时不可用，请稍后重试。"
    return "UPSTREAM_ERROR", f"模型服务返回异常（HTTP {status}），请稍后重试。"


@app.post("/api/chat")
async def chat(body: ChatBody, request: Request, user: sqlite3.Row = Depends(get_current_user)):
    message = (body.message or "").strip()
    if not body.regenerate and not message:
        raise ApiError(400, "EMPTY_MESSAGE", "消息不能为空")
    if len(message) > Config.MAX_MESSAGE_CHARS:
        raise ApiError(400, "MESSAGE_TOO_LONG",
                       f"消息最多 {Config.MAX_MESSAGE_CHARS} 个字符")
    if body.mode not in Config.MODES:
        raise ApiError(400, "INVALID_MODE", "不支持的模式")
    if body.model not in Config.MODELS:
        raise ApiError(400, "INVALID_MODEL", "不支持的模型")
    if body.regenerate and not body.conversation_id:
        raise ApiError(400, "INVALID_REGENERATE", "重新生成需要指定会话")

    rid = getattr(request.state, "request_id", "")
    day = today_str()
    with get_db() as conn:
        reserve_quota(conn, user)
        try:
            conv = None
            if body.conversation_id:
                conv = conn.execute(
                    "SELECT * FROM conversations WHERE id=? AND user_id=?",
                    (body.conversation_id, user["id"]),
                ).fetchone()
                if conv is None:
                    raise ApiError(404, "NOT_FOUND", "会话不存在")
            elif body.regenerate:
                raise ApiError(400, "INVALID_REGENERATE", "重新生成需要指定会话")
            else:
                conv_id = uuid.uuid4().hex
                title = message.replace("\n", " ")[:24]
                now = now_ts()
                with transaction(conn):
                    conn.execute(
                        "INSERT INTO conversations(id, user_id, title, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)", (conv_id, user["id"], title, now, now)
                    )
                conv = conn.execute("SELECT * FROM conversations WHERE id=?",
                                    (conv_id,)).fetchone()

            now = now_ts()
            if body.regenerate:
                last = conn.execute(
                    "SELECT * FROM messages WHERE conv_id=? AND user_id=? "
                    "ORDER BY rowid DESC LIMIT 1", (conv["id"], user["id"])
                ).fetchone()
                if last is None or last["role"] != "assistant":
                    raise ApiError(400, "NOTHING_TO_REGENERATE", "没有可重新生成的回复")
                last_user = conn.execute(
                    "SELECT content FROM messages WHERE conv_id=? AND user_id=? AND role='user' "
                    "ORDER BY rowid DESC LIMIT 1", (conv["id"], user["id"])
                ).fetchone()
                if last_user is None:
                    raise ApiError(400, "NOTHING_TO_REGENERATE", "没有可重新生成的回复")
                message = last_user["content"]
                with transaction(conn):
                    conn.execute("DELETE FROM messages WHERE id=?", (last["id"],))
                    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                                 (now, conv["id"]))
            else:
                with transaction(conn):
                    conn.execute(
                        "INSERT INTO messages(id, conv_id, user_id, role, content, created_at) "
                        "VALUES (?, ?, ?, 'user', ?, ?)",
                        (uuid.uuid4().hex, conv["id"], user["id"], message, now),
                    )
                    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                                 (now, conv["id"]))

            history = conn.execute(
                "SELECT role, content FROM messages WHERE conv_id=? AND user_id=? "
                "ORDER BY rowid DESC LIMIT ?",
                (conv["id"], user["id"], Config.MAX_HISTORY_MESSAGES),
            ).fetchall()
            history = list(reversed(history))
        except BaseException:
            # 占位已发生但请求未能进入上游：退回当日次数
            refund_message_slot(conn, user["id"], day)
            raise

    base_prompt = Config.STYLE_PROMPT if body.mode == "rewrite" else Config.BASE_PROMPT
    system_prompt = base_prompt + "\n\n# 本轮任务\n" + Config.MODES[body.mode]
    upstream_messages = [{"role": "system", "content": system_prompt}]
    upstream_messages += [
        {"role": h["role"], "content": h["content"]} for h in history if h["content"]
    ]
    payload = {
        "model": body.model,
        "messages": upstream_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": Config.TEMPERATURE,
        "max_tokens": Config.MAX_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async def generate():
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict | None = None
        saved = False

        # 先发 meta：客户端中途断开也能立刻拿到会话 ID，避免“消息已落库但前端不知道”
        yield sse("meta", {"conversation_id": conv["id"], "title": conv["title"],
                           "request_id": rid})

        def persist(final_usage: dict | None):
            nonlocal saved
            if saved:
                return
            saved = True
            text = "".join(content_parts).strip()
            reason = "".join(reasoning_parts).strip()
            if not text:
                return
            input_tokens = int((final_usage or {}).get("prompt_tokens", 0) or 0)
            output_tokens = int((final_usage or {}).get("completion_tokens", 0) or 0)
            with get_db() as conn2:
                with transaction(conn2):
                    conn2.execute(
                        "INSERT INTO messages(id, conv_id, user_id, role, content, reasoning, "
                        "model, input_tokens, output_tokens, created_at) "
                        "VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?)",
                        (uuid.uuid4().hex, conv["id"], user["id"], text, reason,
                         body.model, input_tokens, output_tokens, now_ts()),
                    )
                    conn2.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                                  (now_ts(), conv["id"]))
                if input_tokens or output_tokens:
                    add_token_usage(conn2, user["id"], day, input_tokens, output_tokens)

        try:
            timeout = httpx.Timeout(
                connect=10.0, read=float(Config.UPSTREAM_TIMEOUT),
                write=30.0, pool=10.0,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", Config.DEEPSEEK_API_URL,
                                         headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        code, msg = _upstream_error_message(resp.status_code)
                        with get_db() as conn2:
                            refund_message_slot(conn2, user["id"], day)
                        logger.warning("上游失败 rid=%s status=%s", rid, resp.status_code)
                        yield sse("error", {"code": code, "message": msg,
                                            "request_id": rid,
                                            "details": {"upstream_status": resp.status_code}})
                        return

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("上游返回非 JSON 片段 rid=%s", rid)
                            continue
                        if evt.get("usage"):
                            usage = evt["usage"]
                        choices = evt.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        reason = delta.get("reasoning_content")
                        if reason:
                            reasoning_parts.append(reason)
                            yield sse("reasoning", {"content": reason})
                        piece = delta.get("content")
                        if piece:
                            content_parts.append(piece)
                            yield sse("delta", {"content": piece})

            persist(usage)
            yield sse("done", {
                "conversation_id": conv["id"],
                "title": conv["title"],
                "usage": {
                    "input": int((usage or {}).get("prompt_tokens", 0) or 0),
                    "output": int((usage or {}).get("completion_tokens", 0) or 0),
                },
                "request_id": rid,
            })
        except asyncio.CancelledError:
            # 客户端中途关闭：已生成内容仍然落库，避免“看过的内容消失”
            persist(usage)
            logger.info("客户端中断 rid=%s conv=%s saved=%s", rid, conv["id"], saved)
            raise
        except httpx.TimeoutException:
            if not content_parts:
                with get_db() as conn2:
                    refund_message_slot(conn2, user["id"], day)
            logger.warning("上游超时 rid=%s", rid)
            yield sse("error", {"code": "UPSTREAM_TIMEOUT",
                                "message": "模型响应超时，请稍后重试。",
                                "request_id": rid, "details": {}})
        except httpx.HTTPError as exc:
            if not content_parts:
                with get_db() as conn2:
                    refund_message_slot(conn2, user["id"], day)
            logger.warning("上游网络错误 rid=%s err=%s", rid, type(exc).__name__)
            yield sse("error", {"code": "UPSTREAM_NETWORK",
                                "message": "无法连接模型服务，请检查网络后重试。",
                                "request_id": rid, "details": {}})
        finally:
            persist(usage)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================================
# 管理后台
# ============================================================================

@app.get("/api/admin/overview")
async def admin_overview(admin: sqlite3.Row = Depends(require_admin)):
    day, month = today_str(), month_str()
    with get_db() as conn:
        users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        disabled = conn.execute("SELECT COUNT(*) AS c FROM users WHERE disabled=1").fetchone()["c"]
        today = conn.execute(
            "SELECT COALESCE(SUM(messages),0) AS m, "
            "COALESCE(SUM(input_tokens+output_tokens),0) AS t "
            "FROM usage_daily WHERE day=?", (day,)
        ).fetchone()
        monthrow = conn.execute(
            "SELECT COALESCE(SUM(messages),0) AS m, "
            "COALESCE(SUM(input_tokens+output_tokens),0) AS t "
            "FROM usage_daily WHERE day LIKE ?", (month + "-%",)
        ).fetchone()
        active = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM usage_daily WHERE day=?", (day,)
        ).fetchone()["c"]
        top = conn.execute(
            "SELECT u.email, COALESCE(SUM(d.messages),0) AS messages, "
            "COALESCE(SUM(d.input_tokens+d.output_tokens),0) AS tokens "
            "FROM usage_daily d JOIN users u ON u.id=d.user_id "
            "WHERE d.day LIKE ? GROUP BY d.user_id ORDER BY tokens DESC LIMIT 10",
            (month + "-%",),
        ).fetchall()
    return {
        "day": day,
        "month": month,
        "users": users,
        "disabled_users": disabled,
        "active_users_today": active,
        "today": {"messages": today["m"], "tokens": today["t"]},
        "monthly": {"messages": monthrow["m"], "tokens": monthrow["t"]},
        "top_users": [
            {"email": r["email"], "messages": r["messages"], "tokens": r["tokens"]}
            for r in top
        ],
    }


@app.get("/api/admin/users")
async def admin_users(query: str = "", admin: sqlite3.Row = Depends(require_admin)):
    q = (query or "").strip()
    with get_db() as conn:
        if q:
            rows = conn.execute(
                "SELECT * FROM users WHERE email LIKE ? OR nickname LIKE ? "
                "ORDER BY created_at DESC LIMIT 200",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        result = []
        for u in rows:
            usage = usage_snapshot(conn, u)
            result.append(public_user(u, usage))
    return {"users": result, "count": len(result)}


@app.patch("/api/admin/users/{user_id}")
async def admin_patch_user(user_id: str, body: AdminUserPatch,
                           admin: sqlite3.Row = Depends(require_admin)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise ApiError(400, "EMPTY_PATCH", "没有需要修改的字段")

    if "daily_limit" in fields and fields["daily_limit"] is not None:
        if fields["daily_limit"] != -1 and fields["daily_limit"] < 1:
            raise ApiError(400, "INVALID_LIMIT", "每日额度只能是 -1（不限）或 >=1")
    if "monthly_token_limit" in fields and fields["monthly_token_limit"] is not None:
        if fields["monthly_token_limit"] != -1 and fields["monthly_token_limit"] < 1:
            raise ApiError(400, "INVALID_LIMIT", "月度 token 额度只能是 -1（不限）或 >=1")
    if "role" in fields and fields["role"] not in ("user", "admin"):
        raise ApiError(400, "INVALID_ROLE", "角色只能是 user 或 admin")

    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if target is None:
            raise ApiError(404, "NOT_FOUND", "用户不存在")

        removing_admin = (
            target["role"] == "admin" and (
                fields.get("role") == "user" or fields.get("disabled") is True
            )
        )
        if removing_admin:
            admins = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND disabled=0"
            ).fetchone()["c"]
            if admins <= 1:
                raise ApiError(409, "LAST_ADMIN", "不能禁用或降级最后一个管理员")

        sets, values = [], []
        for key in ("disabled", "daily_limit", "monthly_token_limit", "role"):
            if key in fields:
                value = fields[key]
                if key == "disabled" and value is not None:
                    value = 1 if value else 0
                sets.append(f"{key}=?")
                values.append(value)
        with transaction(conn):
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*values, user_id))
            audit(conn, admin["id"], user_id, "update_user", fields)
            updated = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            usage = usage_snapshot(conn, updated)

    logger.info("管理员操作 admin=%s target=%s fields=%s", admin["id"], user_id, fields)
    return {"success": True, "user": public_user(updated, usage)}


@app.get("/api/admin/audit")
async def admin_audit(limit: int = 100, admin: sqlite3.Row = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM admin_audit ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [
        {"id": r["id"], "admin_id": r["admin_id"], "target_id": r["target_id"],
         "action": r["action"], "details": json.loads(r["details"] or "{}"),
         "created_at": iso(r["created_at"])}
        for r in rows
    ]}
