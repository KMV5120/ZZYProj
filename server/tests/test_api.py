import json

from auth import create_token
from conftest import auth, make_user
from db import get_db, now_ts


def request_and_register(client, mail, email, password="password123"):
    r = client.post("/api/auth/request-code",
                    json={"email": email, "password": password, "nickname": "测试"})
    assert r.status_code == 200, r.text
    code = [c for e, c in mail if e == email.lower()][-1]
    r = client.post("/api/auth/register", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def test_smtp_unconfigured_fails_closed(client):
    r = client.post("/api/auth/request-code",
                    json={"email": "a@example.com", "password": "password123"})
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "MAIL_NOT_CONFIGURED"
    assert body["request_id"].startswith("zzy-")
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM email_codes").fetchone()["c"] == 0


def test_register_login_and_me(client, mail):
    data = request_and_register(client, mail, "new@example.com")
    assert data["token"]
    assert data["user"]["role"] == "user"
    assert data["user"]["email"] == "new@example.com"

    r = client.post("/api/auth/login",
                    json={"email": "NEW@example.com", "password": "password123"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = client.get("/api/auth/me", headers=auth(token))
    assert r.status_code == 200
    usage = r.json()["user"]["usage"]
    assert usage["daily"]["used"] == 0
    assert usage["daily"]["limit"] == 30

    with get_db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE email=?",
                           ("new@example.com",)).fetchone()
        assert row["password_hash"] != "password123"
        assert row["password_hash"].startswith("$2")


def test_admin_bootstrap_by_email(client, mail):
    data = request_and_register(client, mail, "admin@example.com")
    assert data["user"]["role"] == "admin"


def test_wrong_code_attempts_are_counted(client, mail):
    email = "wrong@example.com"
    r = client.post("/api/auth/request-code",
                    json={"email": email, "password": "password123"})
    assert r.status_code == 200
    real = [c for e, c in mail if e == email][-1]
    bad = "000000" if real != "000000" else "111111"

    for i in range(5):
        r = client.post("/api/auth/register", json={"email": email, "code": bad})
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "CODE_MISMATCH"
        assert r.json()["details"]["attempts_left"] == 4 - i

    r = client.post("/api/auth/register", json={"email": email, "code": real})
    assert r.status_code == 429
    assert r.json()["code"] == "CODE_ATTEMPTS"

    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0


def test_expired_code(client, mail):
    email = "expired@example.com"
    assert client.post("/api/auth/request-code",
                       json={"email": email, "password": "password123"}).status_code == 200
    code = [c for e, c in mail if e == email][-1]
    with get_db() as conn:
        conn.execute("UPDATE email_codes SET expires_at=? WHERE email=?", (now_ts() - 1, email))
    r = client.post("/api/auth/register", json={"email": email, "code": code})
    assert r.status_code == 400
    assert r.json()["code"] == "CODE_EXPIRED"


def test_login_does_not_reveal_email_existence(client):
    make_user("exists@example.com")
    r1 = client.post("/api/auth/login",
                     json={"email": "exists@example.com", "password": "wrong-password"})
    r2 = client.post("/api/auth/login",
                     json={"email": "nobody@example.com", "password": "wrong-password"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["message"] == r2.json()["message"]


def test_disabled_user_rejected(client):
    user_id = make_user("banned@example.com")
    token = create_token(user_id)
    with get_db() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (user_id,))
    r = client.get("/api/auth/me", headers=auth(token))
    assert r.status_code == 403
    assert r.json()["code"] == "ACCOUNT_DISABLED"
    r = client.post("/api/auth/login",
                    json={"email": "banned@example.com", "password": "password123"})
    assert r.status_code == 403


def test_conversation_isolation(client):
    a_id = make_user("a@example.com")
    b_id = make_user("b@example.com")
    ta, tb = create_token(a_id), create_token(b_id)

    r = client.post("/api/conversations", headers=auth(tb))
    assert r.status_code == 200
    conv_id = r.json()["conversation"]["id"]

    assert client.get(f"/api/conversations/{conv_id}", headers=auth(ta)).status_code == 404
    assert client.delete(f"/api/conversations/{conv_id}", headers=auth(ta)).status_code == 404
    assert client.get("/api/conversations", headers=auth(ta)).json()["conversations"] == []
    assert len(client.get("/api/conversations", headers=auth(tb)).json()["conversations"]) == 1


def test_chat_stream_persists_and_records_usage(client, use_fake_upstream):
    user_id = make_user("chat@example.com")
    token = create_token(user_id)

    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "你好", "mode": "ask", "model": "deepseek-flash",
    }) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())

    assert "event: delta" in body
    assert "event: reasoning" in body
    assert "event: done" in body
    assert "你好" in body
    done_line = body.split("event: done", 1)[1].split("data: ", 1)[1].split("\n", 1)[0]
    done = json.loads(done_line)
    assert done["usage"] == {"input": 10, "output": 5}

    # 契约：服务端只收到对象；system prompt 来自服务端；无 sk- 泄漏
    sent = use_fake_upstream.last_body
    assert isinstance(sent["messages"], list)
    assert sent["messages"][0]["role"] == "system"
    assert "提问" in sent["messages"][0]["content"]
    assert sent["stream_options"] == {"include_usage": True}
    assert "sk-" not in json.dumps(sent)

    conv_id = done["conversation_id"]
    detail = client.get(f"/api/conversations/{conv_id}", headers=auth(token)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "你好！"
    assert detail["messages"][1]["reasoning"] == "先想一下"

    me = client.get("/api/auth/me", headers=auth(token)).json()["user"]
    assert me["usage"]["daily"]["used"] == 1
    assert me["usage"]["monthly"]["tokens_used"] == 15


def test_chat_followup_includes_history(client, use_fake_upstream):
    user_id = make_user("follow@example.com")
    token = create_token(user_id)
    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "第一句", "mode": "ask", "model": "deepseek-flash",
    }) as r:
        body = "".join(r.iter_text())
    conv_id = json.loads(
        body.split("event: done", 1)[1].split("data: ", 1)[1].split("\n", 1)[0]
    )["conversation_id"]

    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "conversation_id": conv_id, "message": "第二句", "mode": "ask",
        "model": "deepseek-flash",
    }) as r:
        "".join(r.iter_text())

    roles = [m["role"] for m in use_fake_upstream.last_body["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert use_fake_upstream.last_body["messages"][-1]["content"] == "第二句"


def test_regenerate_replaces_last_reply(client, use_fake_upstream):
    user_id = make_user("regen@example.com")
    token = create_token(user_id)
    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "原始问题", "mode": "ask",
    }) as r:
        body = "".join(r.iter_text())
    conv_id = json.loads(
        body.split("event: done", 1)[1].split("data: ", 1)[1].split("\n", 1)[0]
    )["conversation_id"]

    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "conversation_id": conv_id, "regenerate": True, "mode": "ask",
    }) as r:
        assert r.status_code == 200
        body2 = "".join(r.iter_text())
    assert "event: done" in body2

    detail = client.get(f"/api/conversations/{conv_id}", headers=auth(token)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "原始问题"

    # 上游看到的历史里没有旧 assistant 回复，且没有重复插入用户消息
    roles = [m["role"] for m in use_fake_upstream.last_body["messages"]]
    assert roles == ["system", "user"]
    me = client.get("/api/auth/me", headers=auth(token)).json()["user"]
    assert me["usage"]["daily"]["used"] == 2

    # 没有会话时重新生成 → 400
    r = client.post("/api/chat", headers=auth(token),
                    json={"regenerate": True, "mode": "ask"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_REGENERATE"


def test_model_and_all_modes_contract(client, use_fake_upstream):
    from config import Config
    user_id = make_user("modes@example.com")
    token = create_token(user_id)
    for mode in Config.MODES:
        with client.stream("POST", "/api/chat", headers=auth(token), json={
            "message": "测试", "mode": mode, "model": "deepseek-flash",
        }) as r:
            "".join(r.iter_text())
        system = use_fake_upstream.last_body["messages"][0]["content"]
        assert Config.MODES[mode] in system
        assert use_fake_upstream.last_body["model"] == "deepseek-flash"

    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "测试", "mode": "ask", "model": "deepseek-v4-pro",
    }) as r:
        "".join(r.iter_text())
    assert use_fake_upstream.last_body["model"] == "deepseek-v4-pro"


def test_upstream_error_mapping(client, use_fake_upstream):
    user_id = make_user("errors@example.com")
    token = create_token(user_id)
    expected = {
        401: "UPSTREAM_AUTH",
        402: "UPSTREAM_BALANCE",
        429: "UPSTREAM_RATE_LIMIT",
        500: "UPSTREAM_UNAVAILABLE",
    }
    for status, code in expected.items():
        use_fake_upstream.status = status
        with client.stream("POST", "/api/chat", headers=auth(token), json={
            "message": "hi", "mode": "ask",
        }) as r:
            body = "".join(r.iter_text())
        assert code in body, (status, body)
        assert "request_id" in body
    me = client.get("/api/auth/me", headers=auth(token)).json()["user"]
    assert me["usage"]["daily"]["used"] == 0  # 全部退回


def test_request_code_ip_daily_limit(client, mail):
    for i in range(10):
        r = client.post("/api/auth/request-code", json={
            "email": f"ip{i}@example.com", "password": "password123",
        })
        assert r.status_code == 200, (i, r.text)
    r = client.post("/api/auth/request-code", json={
        "email": "ip-over@example.com", "password": "password123",
    })
    assert r.status_code == 429
    assert r.json()["code"] == "RATE_LIMIT_IP"


def test_request_code_resend_cooldown(client, mail):
    email = "cooldown@example.com"
    r = client.post("/api/auth/request-code",
                    json={"email": email, "password": "password123"})
    assert r.status_code == 200
    r = client.post("/api/auth/request-code",
                    json={"email": email, "password": "password123"})
    assert r.status_code == 429
    assert r.json()["code"] == "RATE_LIMIT_EMAIL"


def test_invalid_inputs(client, use_fake_upstream):
    user_id = make_user("invalid@example.com")
    token = create_token(user_id)
    cases = [
        ({"message": "", "mode": "ask"}, "EMPTY_MESSAGE"),
        ({"message": "hi", "mode": "hack"}, "INVALID_MODE"),
        ({"message": "hi", "mode": "ask", "model": "gpt-4"}, "INVALID_MODEL"),
        ({"message": "x" * 8001, "mode": "ask"}, "MESSAGE_TOO_LONG"),
    ]
    for payload, code in cases:
        r = client.post("/api/chat", headers=auth(token), json=payload)
        assert r.status_code == 400, r.text
        assert r.json()["code"] == code
    assert use_fake_upstream.calls == 0


def test_daily_quota(client, use_fake_upstream):
    user_id = make_user("quota@example.com", daily_limit=1)
    token = create_token(user_id)
    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "第一次", "mode": "ask",
    }) as r:
        "".join(r.iter_text())

    r = client.post("/api/chat", headers=auth(token), json={
        "message": "第二次", "mode": "ask",
    })
    assert r.status_code == 429
    assert r.json()["code"] == "QUOTA_DAILY"
    assert r.json()["details"]["limit"] == 1
    assert use_fake_upstream.calls == 1


def test_monthly_token_quota(client, use_fake_upstream):
    user_id = make_user("tokens@example.com", monthly_token_limit=100)
    token = create_token(user_id)
    from db import today_str
    with get_db() as conn:
        conn.execute(
            "INSERT INTO usage_daily(user_id, day, messages, input_tokens, output_tokens) "
            "VALUES (?, ?, 0, 90, 20)", (user_id, today_str())
        )
    r = client.post("/api/chat", headers=auth(token), json={"message": "hi", "mode": "ask"})
    assert r.status_code == 429
    assert r.json()["code"] == "QUOTA_MONTHLY"
    assert use_fake_upstream.calls == 0


def test_upstream_error_is_visible_and_refunds(client, use_fake_upstream):
    user_id = make_user("upstream@example.com")
    token = create_token(user_id)
    use_fake_upstream.status = 402
    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "hello", "mode": "ask",
    }) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body
    assert "UPSTREAM_BALANCE" in body
    assert "余额不足" in body
    me = client.get("/api/auth/me", headers=auth(token)).json()["user"]
    assert me["usage"]["daily"]["used"] == 0


def test_delete_conversation_cascades(client, use_fake_upstream):
    user_id = make_user("del@example.com")
    token = create_token(user_id)
    with client.stream("POST", "/api/chat", headers=auth(token), json={
        "message": "待删除", "mode": "ask",
    }) as r:
        body = "".join(r.iter_text())
    conv_id = json.loads(
        body.split("event: done", 1)[1].split("data: ", 1)[1].split("\n", 1)[0]
    )["conversation_id"]
    assert client.delete(f"/api/conversations/{conv_id}", headers=auth(token)).status_code == 200
    assert client.get(f"/api/conversations/{conv_id}", headers=auth(token)).status_code == 404
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM messages WHERE conv_id=?",
                            (conv_id,)).fetchone()["c"] == 0


def test_admin_patch_and_audit(client, admin_token):
    admin_header = auth(admin_token[0])
    target_id = make_user("target@example.com")

    overview = client.get("/api/admin/overview", headers=admin_header).json()
    assert overview["users"] >= 2

    r = client.patch(f"/api/admin/users/{target_id}", headers=admin_header,
                     json={"disabled": True, "daily_limit": 2})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["disabled"] is True
    assert r.json()["user"]["limits"]["daily_messages"] == 2

    with get_db() as conn:
        audit = conn.execute("SELECT * FROM admin_audit").fetchall()
    assert len(audit) == 1
    assert audit[0]["action"] == "update_user"

    r = client.patch(f"/api/admin/users/{target_id}", headers=admin_header,
                     json={"daily_limit": 0})
    assert r.status_code == 400


def test_admin_permissions(client, user_token, admin_token):
    r = client.get("/api/admin/overview", headers=auth(user_token[0]))
    assert r.status_code == 403
    r = client.patch(f"/api/admin/users/{user_token[1]}", headers=auth(user_token[0]),
                     json={"disabled": True})
    assert r.status_code == 403


def test_last_admin_cannot_demote_self(client, admin_token):
    r = client.patch(f"/api/admin/users/{admin_token[1]}", headers=auth(admin_token[0]),
                     json={"role": "user"})
    assert r.status_code == 409
    assert r.json()["code"] == "LAST_ADMIN"


def test_change_password(client):
    user_id = make_user("chpwd@example.com")
    token = create_token(user_id)
    r = client.post("/api/auth/change-password", headers=auth(token),
                    json={"old_password": "password123", "new_password": "newpassword456"})
    assert r.status_code == 200, r.text

    r = client.post("/api/auth/login",
                    json={"email": "chpwd@example.com", "password": "password123"})
    assert r.status_code == 401
    r = client.post("/api/auth/login",
                    json={"email": "chpwd@example.com", "password": "newpassword456"})
    assert r.status_code == 200

    r = client.post("/api/auth/change-password", headers=auth(token),
                    json={"old_password": "wrong-password", "new_password": "anotherpass789"})
    assert r.status_code == 400
    assert r.json()["code"] == "WRONG_PASSWORD"

    r = client.post("/api/auth/change-password", headers=auth(token),
                    json={"old_password": "newpassword456", "new_password": "newpassword456"})
    assert r.status_code == 400
    assert r.json()["code"] == "SAME_PASSWORD"


def test_validation_error_is_readable(client):
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body["message"], str)
    assert "请求参数不合法" in body["message"]
    assert "[object Object]" not in body["message"]


def test_invalid_token_envelope(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"
    assert r.json()["request_id"].startswith("zzy-")
