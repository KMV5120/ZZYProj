# ============================================================================
# ZZyProj 平台 - 认证原语
# 密码 bcrypt；令牌 PyJWT HS256；验证码只存哈希。
# ============================================================================

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from config import Config

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email))


def validate_password(password: str) -> str | None:
    """返回错误文案；None 表示通过。"""
    if not isinstance(password, str):
        return "密码格式不正确"
    if len(password) < Config.PASSWORD_MIN:
        return f"密码至少 {Config.PASSWORD_MIN} 位"
    if len(password) > Config.PASSWORD_MAX:
        return f"密码最多 {Config.PASSWORD_MAX} 位"
    if any(ord(c) < 32 for c in password):
        return "密码不能包含控制字符"
    return None


def _encode(password: str) -> bytes:
    # bcrypt 只取前 72 字节；先归一为 UTF-8，避免截断在多字节字符中间
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "aud": Config.JWT_AUDIENCE,
        "iss": "zzy-proj",
        "iat": now,
        "exp": now + timedelta(days=Config.JWT_TTL_DAYS),
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")


def verify_token(token: str) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            Config.JWT_SECRET,
            algorithms=["HS256"],
            audience=Config.JWT_AUDIENCE,
            issuer="zzy-proj",
        )
    except jwt.PyJWTError:
        return None


def hash_code(email: str, code: str) -> str:
    """验证码只存哈希；与 JWT 密钥绑定，库泄露也无法直接还原。"""
    msg = f"{normalize_email(email)}:{code}".encode("utf-8")
    return hmac.new(Config.JWT_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def code_matches(email: str, code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_code(email, code), stored_hash)
