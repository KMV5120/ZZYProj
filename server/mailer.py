# ============================================================================
# ZZyProj 平台 - 邮件发送
# 明确 fail closed：未配置 SMTP 时抛 MailNotConfigured，调用方返回 503。
# 不复制学习助手 email_service 的“未配置也返回成功”行为。
# ============================================================================

import smtplib
import ssl
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from config import Config


class MailNotConfigured(Exception):
    pass


class MailError(Exception):
    pass


def build_code_email(code: str) -> MIMEText:
    minutes = Config.CODE_TTL_SECONDS // 60
    body = (
        f"你的验证码是：{code}\n\n"
        f"{minutes} 分钟内有效，最多可尝试 {Config.CODE_MAX_ATTEMPTS} 次。\n"
        "如果不是你本人操作，请忽略这封邮件。请勿把验证码发给任何人。\n\n"
        f"—— {Config.APP_NAME}"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(f"【{Config.APP_NAME}】注册验证码", "utf-8")
    msg["From"] = formataddr((Config.SMTP_FROM_NAME, Config.SMTP_FROM))
    msg["To"] = ""
    return msg


def send_verification_code(email: str, code: str) -> None:
    """发送验证码。成功返回 None；失败抛异常，由调用方决定 HTTP 状态。"""
    if not Config.smtp_configured():
        raise MailNotConfigured("SMTP 未配置")

    msg = build_code_email(code)
    msg["To"] = email
    context = ssl.create_default_context()

    server = None
    try:
        if Config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(
                Config.SMTP_HOST, Config.SMTP_PORT,
                timeout=Config.SMTP_TIMEOUT, context=context,
            )
        else:
            server = smtplib.SMTP(
                Config.SMTP_HOST, Config.SMTP_PORT, timeout=Config.SMTP_TIMEOUT
            )
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()

        server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        server.sendmail(Config.SMTP_FROM, [email], msg.as_string())
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        # 只把异常类型与文本交给上层；密码不在异常文本里
        raise MailError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
