import os
import tempfile
import threading
import time
import uuid

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

_TMP = tempfile.mkdtemp(prefix="zzy-test-")
os.environ.update({
    "ZZY_ENV": "test",
    "ZZY_DATA_DIR": _TMP,
    "ZZY_JWT_SECRET": "t" * 64,
    "DEEPSEEK_API_KEY": "test-key",
    "ZZY_ADMIN_EMAILS": "admin@example.com",
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "ZZY_DAILY_LIMIT": "30",
    "ZZY_MONTHLY_TOKEN_LIMIT": "1000000",
})

import main as zzy_main  # noqa: E402
from auth import create_token, hash_password  # noqa: E402
from db import get_db, init_db, now_ts  # noqa: E402

TABLES = ["admin_audit", "usage_daily", "messages", "conversations", "email_codes", "users"]


def clear_db():
    init_db()
    with get_db() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    clear_db()
    zzy_main.limiter._hits.clear()
    with TestClient(zzy_main.app) as c:
        yield c


@pytest.fixture
def mail(monkeypatch):
    sent: list[tuple[str, str]] = []

    def fake_send(email: str, code: str) -> None:
        sent.append((email, code))

    monkeypatch.setattr(zzy_main, "send_verification_code", fake_send)
    # 真实发送被替身接管；同时让“SMTP 已配置”前置检查通过
    monkeypatch.setattr(zzy_main.Config, "smtp_configured", lambda: True)
    return sent


def make_user(email: str, password: str = "password123", role: str = "user",
              daily_limit=None, monthly_token_limit=None, disabled: int = 0) -> str:
    user_id = uuid.uuid4().hex
    now = now_ts()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash, nickname, role, disabled, "
            "email_verified, created_at, last_login, daily_limit, monthly_token_limit) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (user_id, email.lower(), hash_password(password), email.split("@")[0],
             role, disabled, now, now, daily_limit, monthly_token_limit),
        )
    return user_id


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class FakeUpstream:
    def __init__(self):
        self.status = 200
        self.last_body: dict | None = None
        self.calls = 0
        app = FastAPI()

        @app.post("/chat/completions")
        async def chat(request: Request):
            self.calls += 1
            self.last_body = await request.json()
            if self.status != 200:
                return JSONResponse({"error": {"message": "fake upstream"}},
                                    status_code=self.status)

            async def gen():
                yield 'data: {"choices":[{"delta":{"reasoning_content":"先想一下"}}]}\n\n'
                yield 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                yield 'data: {"choices":[{"delta":{"content":"！"}}]}\n\n'
                yield 'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        self.app = app
        self.server = None
        self.thread = None

    def start(self, port: int = 8199):
        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("fake upstream 启动失败")

    def stop(self):
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def fake_upstream():
    upstream = FakeUpstream()
    upstream.start()
    yield upstream
    upstream.stop()


@pytest.fixture
def use_fake_upstream(monkeypatch, fake_upstream):
    fake_upstream.status = 200
    fake_upstream.last_body = None
    fake_upstream.calls = 0
    monkeypatch.setattr(
        zzy_main.Config, "DEEPSEEK_API_URL",
        "http://127.0.0.1:8199/chat/completions",
    )
    return fake_upstream


@pytest.fixture
def user_token():
    user_id = make_user("user@example.com")
    return create_token(user_id), user_id


@pytest.fixture
def admin_token():
    user_id = make_user("admin@example.com", role="admin")
    return create_token(user_id), user_id
