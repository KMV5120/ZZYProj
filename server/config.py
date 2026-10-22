# ============================================================================
# ZZyProj 平台 - 配置（唯一事实源）
# 规则：模型白名单、模式提示词、默认额度、限制值只允许在这里定义。
# ============================================================================

import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if raw == "":
        return default
    return int(raw)


class Config:
    APP_NAME = "轻量化语言模型"
    VERSION = os.getenv("ZZY_VERSION", "ZZY_1.0.0")
    ENV = os.getenv("ZZY_ENV", "dev").strip().lower()

    # 数据
    DATA_DIR = os.getenv("ZZY_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    DB_PATH = os.path.join(DATA_DIR, "zzy.db")
    SITE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site"
    )

    # 认证
    JWT_SECRET = os.getenv("ZZY_JWT_SECRET", "").strip()
    JWT_AUDIENCE = "zzy"
    JWT_TTL_DAYS = _int("ZZY_JWT_TTL_DAYS", 30)
    PASSWORD_MIN = 8
    PASSWORD_MAX = 128

    # 模型
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
    DEEPSEEK_API_URL = os.getenv(
        "DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"
    )
    MODELS = ("deepseek-flash", "deepseek-v4-pro")
    DEFAULT_MODEL = "deepseek-flash"
    UPSTREAM_TIMEOUT = _int("ZZY_UPSTREAM_TIMEOUT", 180)
    MAX_TOKENS = _int("ZZY_MAX_TOKENS", 4096)
    TEMPERATURE = float(os.getenv("ZZY_TEMPERATURE", "0.7"))
    MAX_MESSAGE_CHARS = _int("ZZY_MAX_MESSAGE_CHARS", 8000)
    MAX_HISTORY_MESSAGES = _int("ZZY_MAX_HISTORY_MESSAGES", 40)

    # 配额（NULL=用这里的默认值；-1=不限；>0=自定义）
    DAILY_MESSAGE_LIMIT = _int("ZZY_DAILY_LIMIT", 30)
    MONTHLY_TOKEN_LIMIT = _int("ZZY_MONTHLY_TOKEN_LIMIT", 1_000_000)

    # 邮件（未配置时注册接口 fail closed）
    SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
    SMTP_PORT = _int("SMTP_PORT", 465)
    SMTP_USER = os.getenv("SMTP_USER", "").strip()
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
    SMTP_FROM = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", APP_NAME)
    SMTP_TIMEOUT = _int("SMTP_TIMEOUT", 15)

    # 管理员（逗号分隔，启动时归一为小写）
    ADMIN_EMAILS = {
        e.strip().lower()
        for e in os.getenv("ZZY_ADMIN_EMAILS", "").split(",")
        if e.strip()
    }

    # 模式提示词 —— 前端只发 mode id，正文只在服务端
    MODES = {
        "rewrite": (
            "用户在改写文字。保留原意和事实，去掉 AI 腔、翻译腔和八股腔，"
            "换成自然的人话。直接给改好的成品，不要解释写作思路。"
        ),
        "create": (
            "用户想让你写一段新内容。直接给成品，不要提纲、不要说明写作思路。"
        ),
        "ask": (
            "用户在提问。用你自己的语气回答，说人话，别写成百科条目，"
            "也别急着分点。"
        ),
        "academic": (
            "用户在润色学术文字。保持准确克制，但把翻译腔、八股腔去掉，"
            "让它像人认真写的。"
        ),
        "code": "用户在问代码。代码要能跑，说明用大白话，别堆术语。",
    }
    BASE_PROMPT = (
        "你是「轻量化语言模型」，由 KMV 学习助手平台提供运行支持。"
        "回答简洁、具体、可执行；不使用夸张营销语气。"
    )
    # 原站「去 AI 味」角色提示词：只在服务端保存，前端不再下发
    STYLE_PROMPT = """你是一个具有独立思考能力的文字创作者，拥有独特的个人风格和表达习惯。
你的任务是将输入的文本改写为自然、真实的人类表达。

# 写作风格要求
1. 语言节奏：句子长短交替，避免工整的排比和对称结构
2. 个人印记：适当加入个人观点、主观感受和犹豫语气
3. 逻辑特征：允许跳跃性思维，不必事事论证严密
4. 用词习惯：
   - 使用口语化表达和生活中的惯用词
   - 避免"此外、综上所述、值得注意的是"等过渡词堆砌
   - 适当使用语气词（"吧、呢、嘛、哎"）
   - 允许少量重复用词，不必频繁换同义词
5. 标点习惯：
   - 逗号使用较多，模拟自然停顿
   - 偶尔使用破折号、省略号表示思维中断
   - 问号和感叹号根据实际情绪使用，不必克制

# 禁止特征（AI文本典型痕迹）
1. 不使用"首先、其次、最后"的结构化框架
2. 不使用"让我们、我们来、接下来"等引导语
3. 不使用"值得关注的是、需要指出的是"等强调句式
4. 不使用"在当今社会、随着时代发展"等宏大开头
5. 不使用每段都有主题句的总分总结构
6. 不使用完美的递进逻辑和严密的因果关系
7. 不使用过多专业术语堆砌和学术腔
8. 不追求面面俱到，允许有遗漏和侧重"""

    # 反滥用
    CODE_TTL_SECONDS = 10 * 60
    CODE_MAX_ATTEMPTS = 5
    CODE_RESEND_SECONDS = 60
    CODE_IP_DAILY = 10
    LOGIN_ATTEMPTS_PER_MINUTE = 5

    @classmethod
    def smtp_configured(cls) -> bool:
        return bool(cls.SMTP_HOST and cls.SMTP_USER and cls.SMTP_PASSWORD)

    @classmethod
    def validate(cls) -> list[str]:
        """启动前校验。返回错误列表；非空则拒绝启动（fail closed）。"""
        errors = []
        if len(cls.JWT_SECRET) < 32:
            errors.append("ZZY_JWT_SECRET 缺失或长度 < 32")
        if not cls.DEEPSEEK_API_KEY:
            errors.append("DEEPSEEK_API_KEY 未配置")
        if cls.DAILY_MESSAGE_LIMIT < -1 or cls.DAILY_MESSAGE_LIMIT == 0:
            errors.append("ZZY_DAILY_LIMIT 必须为 -1（不限）或 >=1")
        if cls.MONTHLY_TOKEN_LIMIT < -1 or cls.MONTHLY_TOKEN_LIMIT == 0:
            errors.append("ZZY_MONTHLY_TOKEN_LIMIT 必须为 -1（不限）或 >=1")
        if cls.SMTP_PORT not in (465, 587, 25):
            errors.append("SMTP_PORT 只支持 465(SSL) / 587(STARTTLS) / 25")
        partial = [bool(cls.SMTP_HOST), bool(cls.SMTP_USER), bool(cls.SMTP_PASSWORD)]
        if any(partial) and not all(partial):
            errors.append("SMTP_HOST / SMTP_USER / SMTP_PASSWORD 必须同时配置")
        return errors

    @classmethod
    def masked_summary(cls) -> dict:
        """启动日志用：只打印生效值，不打印秘密。"""
        def mask(s: str, keep: int = 2) -> str:
            if not s:
                return "(empty)"
            return s[:keep] + "***" + s[-1:] if len(s) > keep + 1 else "***"

        return {
            "version": cls.VERSION,
            "env": cls.ENV,
            "data_dir": cls.DATA_DIR,
            "models": list(cls.MODELS),
            "daily_limit": cls.DAILY_MESSAGE_LIMIT,
            "monthly_token_limit": cls.MONTHLY_TOKEN_LIMIT,
            "deepseek_key": mask(cls.DEEPSEEK_API_KEY),
            "jwt_secret": mask(cls.JWT_SECRET),
            "smtp": f"{cls.SMTP_HOST}:{cls.SMTP_PORT}" if cls.SMTP_HOST else "(未配置)",
            "smtp_user": mask(cls.SMTP_USER, 3),
            "admin_emails": sorted(cls.ADMIN_EMAILS),
        }
