# ============================================================================
# ZZyProj 契约 smoke：真实 HTTP + 真实服务进程 + 假上游
# 断言「组合层真正发出去的东西」，不是 mock 两层的单测。
# 运行：PYTHON=/path/to/python ./tools/contract_smoke.py
# ============================================================================

import json
import os
import sys
import tempfile
import threading
import time

APP_PORT = 8102
UPSTREAM_PORT = 8199
TMP = tempfile.mkdtemp(prefix="zzy-smoke-")

os.environ.update({
    "ZZY_ENV": "smoke",
    "ZZY_DATA_DIR": TMP,
    "ZZY_JWT_SECRET": "s" * 64,
    "DEEPSEEK_API_KEY": "test-key",
    "DEEPSEEK_API_URL": f"http://127.0.0.1:{UPSTREAM_PORT}/chat/completions",
    "ZZY_ADMIN_EMAILS": "admin@example.com",
    "ZZY_DAILY_LIMIT": "30",
    "ZZY_MONTHLY_TOKEN_LIMIT": "1000000",
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
})

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from auth import hash_code, hash_password  # noqa: E402
from db import get_db, init_db, now_ts  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)
    print(f"  PASS {name}")


class FakeUpstream:
    def __init__(self):
        self.last_body = None
        self.calls = 0
        app = FastAPI()

        @app.post("/chat/completions")
        async def chat(request: Request):
            self.calls += 1
            self.last_body = await request.json()

            async def gen():
                yield 'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
                yield 'data: {"choices":[{"delta":{"content":"的"}}]}\n\n'
                yield 'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        self.app = app
        self.server = None
        self.thread = None

    def start(self):
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=UPSTREAM_PORT, log_level="error")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        wait_started(self.server)

    def stop(self):
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)


def wait_started(server, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.started:
            return
        time.sleep(0.05)
    raise RuntimeError("服务启动超时")


def seed_code(email, code="123456", password="password123"):
    with get_db() as conn:
        conn.execute("DELETE FROM email_codes WHERE email=?", (email,))
        conn.execute(
            "INSERT INTO email_codes(email, code_hash, password_hash, nickname, purpose, "
            "expires_at, attempts, created_at) VALUES (?, ?, ?, 'smoke', 'register', ?, 0, ?)",
            (email, hash_code(email, code), hash_password(password), now_ts() + 600, now_ts()),
        )


def main():
    init_db()
    upstream = FakeUpstream()
    upstream.start()

    import main as zzy_main
    zzy_main.init_db()
    app_cfg = uvicorn.Config(zzy_main.app, host="127.0.0.1", port=APP_PORT, log_level="warning")
    app_server = uvicorn.Server(app_cfg)
    app_thread = threading.Thread(target=app_server.run, daemon=True)
    app_thread.start()
    wait_started(app_server)

    base = f"http://127.0.0.1:{APP_PORT}"
    try:
        with httpx.Client(timeout=30) as c:
            print("[1] 健康检查与错误信封")
            r = c.get(base + "/healthz")
            check("healthz", r.status_code == 200 and r.json()["status"] == "ok")
            r = c.post(base + "/api/auth/request-code",
                       json={"email": "nobody@example.com", "password": "password123"})
            check("SMTP 未配置 → 503", r.status_code == 503 and r.json()["code"] == "MAIL_NOT_CONFIGURED")
            check("错误信封含 request_id", r.json()["request_id"].startswith("zzy-"))
            r = c.post(base + "/api/auth/login", json={})
            check("422 可读", r.status_code == 422 and "[object Object]" not in r.json()["message"])

            print("[2] 注册 / 登录")
            seed_code("user@example.com")
            r = c.post(base + "/api/auth/register",
                       json={"email": "user@example.com", "code": "123456",
                             "password": "password123"})
            check("注册", r.status_code == 200 and r.json()["user"]["role"] == "user")
            user_token = r.json()["token"]
            r = c.post(base + "/api/auth/login",
                       json={"email": "user@example.com", "password": "password123"})
            check("登录", r.status_code == 200)
            user_token = r.json()["token"]
            uh = {"Authorization": "Bearer " + user_token}

            print("[3] 聊天契约（真实 HTTP + 假上游）")
            with c.stream("POST", base + "/api/chat", headers=uh,
                          json={"message": "你好", "mode": "ask", "model": "deepseek-flash"}) as r:
                raw = "".join(r.iter_text())
            check("SSE 事件", "event: meta" in raw and "event: delta" in raw and "event: done" in raw)
            body = upstream.last_body
            check("请求体是对象（未被双重序列化）", isinstance(body, dict) and isinstance(body["messages"], list))
            check("system prompt 来自服务端", body["messages"][0]["role"] == "system"
                  and "提问" in body["messages"][0]["content"])
            check("usage 计入流式", body["stream_options"] == {"include_usage": True})
            check("密钥不下发", "sk-" not in json.dumps(body) and "test-key" not in raw)

            conv_id = json.loads(
                raw.split("event: done", 1)[1].split("data: ", 1)[1].split("\n", 1)[0]
            )["conversation_id"]
            detail = c.get(base + f"/api/conversations/{conv_id}", headers=uh).json()
            check("落库两条消息", [m["role"] for m in detail["messages"]] == ["user", "assistant"])
            me = c.get(base + "/api/auth/me", headers=uh).json()["user"]
            check("日次数 +1", me["usage"]["daily"]["used"] == 1)
            check("token 记账", me["usage"]["monthly"]["tokens_used"] == 15)

            print("[4] 配额与越权")
            with get_db() as conn:
                conn.execute("UPDATE users SET daily_limit=1 WHERE email='user@example.com'")
            r = c.post(base + "/api/chat", headers=uh,
                       json={"message": "第二条", "mode": "ask"})
            check("日额度 429", r.status_code == 429 and r.json()["code"] == "QUOTA_DAILY")
            check("超限未打上游", upstream.calls == 1)

            seed_code("other@example.com")
            r = c.post(base + "/api/auth/register",
                       json={"email": "other@example.com", "code": "123456",
                             "password": "password123"})
            oh = {"Authorization": "Bearer " + r.json()["token"]}
            check("越权读会话 404", c.get(base + f"/api/conversations/{conv_id}", headers=oh).status_code == 404)
            check("越权删会话 404", c.delete(base + f"/api/conversations/{conv_id}", headers=oh).status_code == 404)

            print("[4b] 并发配额")
            seed_code("race@example.com")
            r = c.post(base + "/api/auth/register",
                       json={"email": "race@example.com", "code": "123456",
                             "password": "password123"})
            rh = {"Authorization": "Bearer " + r.json()["token"]}
            with get_db() as conn:
                conn.execute("UPDATE users SET daily_limit=1 WHERE email='race@example.com'")
            baseline = upstream.calls

            from concurrent.futures import ThreadPoolExecutor

            def fire(_):
                try:
                    rr = httpx.post(base + "/api/chat", headers=rh,
                                    json={"message": "并发", "mode": "ask"}, timeout=30)
                    return rr.status_code
                except Exception:
                    return 0

            with ThreadPoolExecutor(max_workers=5) as pool:
                statuses = list(pool.map(fire, range(5)))
            ok = statuses.count(200)
            limited = statuses.count(429)
            check("并发只放行 1 个", ok == 1 and limited == 4, str(statuses))
            check("并发未超发上游", upstream.calls == baseline + 1,
                  f"{baseline} -> {upstream.calls}")

            print("[5] 管理端")
            seed_code("admin@example.com")
            r = c.post(base + "/api/auth/register",
                       json={"email": "admin@example.com", "code": "123456",
                             "password": "password123"})
            ah = {"Authorization": "Bearer " + r.json()["token"]}
            check("管理员引导", r.json()["user"]["role"] == "admin")
            check("非管理员被拒", c.get(base + "/api/admin/overview", headers=uh).status_code == 403)
            ov = c.get(base + "/api/admin/overview", headers=ah)
            check("overview", ov.status_code == 200 and ov.json()["users"] == 4)
            users = c.get(base + "/api/admin/users", headers=ah).json()["users"]
            target = next(u for u in users if u["email"] == "user@example.com")
            r = c.patch(base + f"/api/admin/users/{target['id']}", headers=ah,
                        json={"daily_limit": 5})
            check("调额度", r.status_code == 200 and r.json()["user"]["limits"]["daily_messages"] == 5)
            audit = c.get(base + "/api/admin/audit", headers=ah).json()["items"]
            check("审计落库", audit and audit[0]["action"] == "update_user")

            print("[6] 静态页与渲染扫描")
            page = c.get(base + "/")
            js = c.get(base + "/static/app.js")
            check("页面 200", page.status_code == 200 and "轻量化语言模型" in page.text)
            check("JS 200", js.status_code == 200 and len(js.text) > 10000)
            check("无 [object Object]", "[object Object]" not in page.text + js.text)
            check("页面无密钥痕迹", "sk-" not in page.text + js.text)

        print(f"\n契约 smoke 通过：{len(PASSED)} 项")
        return 0
    finally:
        upstream.stop()
        app_server.should_exit = True
        app_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
