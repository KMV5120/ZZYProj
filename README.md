# 轻量化语言模型（ZZyProj 平台版）

原项目是一个单页、自带 API key 的静态演示站。本分支把它改造成可独立运营的平台：

- 独立账号（邮箱验证码注册 / 登录，与学习助手账号隔离）
- 对话按用户保存在服务端（SQLite），换设备可继续
- DeepSeek key 只在服务端，前端拿不到
- 每日消息数 + 每月 token 双配额，管理员可按人覆盖
- 管理后台：用量看板、用户列表、禁用/启用、调额度、审计
- 独立容器 `zzy-proj`，挂在学习助手域名 `/ZZYProj/` 子路径；可独立回滚、整体摘除

> 交付基线见 `delivery/20261022-platformify/`（RB / AB / DM / 闸门）。

## 架构

```
浏览器 ──/ZZYProj/──> 宿主机 nginx ──> 127.0.0.1:8100 (zzy-proj 容器)
                                              │
                                              ├─ SQLite (data/zzy/zzy.db)
                                              ├─ 网易 163 SMTP（验证码）
                                              └─ api.deepseek.com（流式代理）
```

与学习助手主站只共享 Docker 网络 `web_app-network` 与域名，不共享进程、数据、密钥。

## 目录

```
site/               前端（index.html + app.js，无构建步骤）
server/             FastAPI 服务（账号 / 会话 / 聊天 / 配额 / 管理）
server/tests/       pytest（含假上游 + 真实 HTTP 契约）
tools/contract_smoke.py  真实服务进程 + 真实 HTTP 的契约 smoke
tools/g2.sh         G2 自动化闸门
deploy/             nginx 安装/摘除、部署、回滚脚本
delivery/           RB / AB / DM / gate
```

## 本地开发

需要 Python 3.11+ 与 `server/requirements-dev.txt` 中的依赖：

```bash
cp .env.example .env      # 填 JWT_SECRET、DEEPSEEK_API_KEY、SMTP
python -m uvicorn main:app --host 127.0.0.1 --port 8100   # 在 server/ 目录
```

打开 `http://127.0.0.1:8100/`。未配置 SMTP 时注册接口返回 503（fail closed），
可用 `delivery/` 里的方式注入验证码做本地验收。

## 测试（G2）

```bash
PYTHON=/path/to/python ./tools/g2.sh
```

包含：26 项 pytest + 29 项真实 HTTP 契约 smoke + 20 项 jsdom 前端接线 smoke
（jsdom 需 `NODE_PATH` 指向含 `jsdom` 的 `node_modules`）。
**真实浏览器逐控件点击仍是人工步骤**，截图放 `evidence/`。

## 部署（G3）

```bash
./deploy/deploy.sh                 # 备份 DB → 构建 → 启动 → 健康检查
./deploy/nginx-install.sh          # 宿主机 nginx 增加 /ZZYProj/（需 sudo）
# 外部 smoke：https://kmv-learning-assistant.xyz/ZZYProj/
./deploy/rollback.sh pre-<版本号>  # 回滚到上一版镜像
./deploy/nginx-uninstall.sh        # 摘除子路径转发
```

上线前必须：`nginx -t` 通过、备份 DB、确认 `git rev-parse HEAD` 与基线一致。

SMTP 不可用时的管理员引导（应急，不经过邮件）：

```bash
PYTHON=/path/to/python ./tools/create_user.py \
  --email ab15630563156@163.com --password '强密码' --role admin
```

## 配置

| 变量 | 说明 |
| --- | --- |
| `ZZY_JWT_SECRET` | ≥32 位随机串，必须配置 |
| `DEEPSEEK_API_KEY` | 只在服务端 |
| `ZZY_DAILY_LIMIT` | 默认每日消息数；`-1` 不限 |
| `ZZY_MONTHLY_TOKEN_LIMIT` | 默认每月 token；`-1` 不限 |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | 网易 163：`smtp.163.com:465` SSL，密码是**授权码** |
| `ZZY_ADMIN_EMAILS` | 初始管理员，逗号分隔 |

未配置 SMTP 时服务仍可启动，但注册接口明确返回 503，不会静默“发送成功”。

## 数据与隐私

- 对话内容按 `user_id` 隔离；越权统一 404。
- 管理后台只提供用量与账号状态，**没有读取对话正文的 API/UI**。
- 拥有宿主机 root 的人仍可直接读 SQLite；这是运维边界，不是产品契约。
- 前端只在 localStorage 存 `zzy_token`、模型与模式；不存对话与密钥。

## 已知边界

- 月度 token 配额在“检查 → 流式结束记账”之间存在小幅竞态（≤ 单次请求 token）；
  日次数在调用上游前原子占位，不会超发。
- 流式中途断网且未收到 `meta` 时，服务端已建的会话可能不会立刻出现在前端；刷新后可见。
- 网易 SMTP 可能把验证码投进垃圾箱；以实际收件为准。
