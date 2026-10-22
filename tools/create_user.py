# ============================================================================
# 本地/应急建号（不依赖 SMTP）
# 用法：PYTHON=/path/to/python ./tools/create_user.py --email a@b.com \
#          --password '至少8位' [--role admin] [--nickname 名字] [--data-dir data/zzy]
# 只在服务端执行；密码不会写日志。
# 生产容器使用 --data-dir data/zzy（容器挂载的数据卷），不要写到开发库。
# ============================================================================

import argparse
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", choices=["user", "admin"], default="user")
    parser.add_argument("--nickname", default="")
    parser.add_argument("--data-dir", default="",
                        help="覆盖 ZZY_DATA_DIR；生产用 data/zzy")
    parser.add_argument("--reset", action="store_true",
                        help="用户已存在时重置密码（管理员应急）")
    args = parser.parse_args()

    # 必须在导入 config/db 之前设置，否则 Config 会读 .env 的开发库路径
    if args.data_dir:
        os.environ["ZZY_DATA_DIR"] = os.path.abspath(args.data_dir)

    from auth import (  # noqa: E402
        hash_password, normalize_email, validate_email, validate_password,
    )
    from config import Config  # noqa: E402
    from db import get_db, init_db, now_ts  # noqa: E402

    print(f"目标库：{Config.DB_PATH}")
    email = normalize_email(args.email)
    if not validate_email(email):
        print("❌ 邮箱格式不正确")
        return 2
    err = validate_password(args.password)
    if err:
        print("❌ " + err)
        return 2

    init_db()
    with get_db() as conn:
        existing = conn.execute("SELECT id, role FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            if args.reset:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (hash_password(args.password), existing["id"]))
                print(f"✅ 已重置密码：{email}（{existing['role']}）")
                return 0
            print(f"ℹ️  已存在：{email}（{existing['role']}），未修改。加 --reset 可重置密码。")
            return 0
        user_id = uuid.uuid4().hex
        now = now_ts()
        conn.execute(
            "INSERT INTO users(id, email, password_hash, nickname, role, disabled, "
            "email_verified, created_at, last_login) VALUES (?, ?, ?, ?, ?, 0, 1, ?, NULL)",
            (user_id, email, hash_password(args.password),
             args.nickname or email.split("@")[0], args.role, now),
        )
    print(f"✅ 已创建：{email}（{args.role}），id={user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
