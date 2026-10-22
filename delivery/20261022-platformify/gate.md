# 四道闸 — ZZyProj 平台化改造

source: RB v1 @ 2026-10-22

## G0 开工闸（≤30 分钟）

- [x] RB v1 冻结（本文同目录 `baseline.md`）
- [x] DM 12 维填完，红项有处理计划（`matrix.md`）
- [x] 范围与“不做”清单确定
- [x] 交付身份块写入 RB
- [x] Q1 已解决；Q2 默认 163 地址；Q3/Q4 不阻塞开发
- 产出：`baseline.md` + `matrix.md`。**G0 通过后才写代码。**

## G1 提交闸（每次提交，<2 分钟）

```bash
cd ZZYProj
.venv/bin/python -m pytest -q          # 单元 + 契约
.venv/bin/python tools/contract_smoke.py  # 真实 client 边界
```

- 提交信息带 RB ID：`feat(zzy): R1/R2 账号与邮件`
- 禁止新增 `except: pass` / `catch {}` / 静默默认值；确需静默必须注释 + 测试 + 可见标记。

## G2 合并/发版闸（<10 分钟，一条命令）

```bash
./tools/g2.sh
```

包含：

1. 全量 pytest（auth / isolation / quota / failure / admin / persistence）；
2. 假上游 + 真实 HTTP 契约 smoke（断言请求体是对象、SSE 事件格式）；
3. 启动真实服务，用 HTTP 走完「注册→登录→建会话→发消息→刷新读取→删除」；
4. 子路径断言（若在容器内跑，走 nginx；本地跑则断言相对路径）；
5. 渲染扫描：页面无 `[object Object]` / `undefined` / `NaN`；
6. 配置负例：缺 JWT/key 启动失败、SMTP 未配注册 503、非法模型 400、越权 404；
7. 持久化回放：重启进程后会话仍在；
8. 浏览器真实点击（A19 / 控件清单），截图入 `evidence/`。

**不通过不合并。**

## G3 上线闸（≤30 分钟）

一页纸 go/no-go：

| 项 | 内容 |
| --- | --- |
| 范围/版本 | 只上 ZZyProj 平台化；不动学习助手主应用代码 |
| 仓库身份 | `git remote -v` / `git rev-parse HEAD` 与 RB 一致 |
| 备份 | `cp data/zzy/zzy.db data/zzy/zzy.db.bak_20261022` + `sha256sum` |
| 构建 | `docker build -t zzy-proj:<ver> .`，旧镜像 `docker tag ... zzy-proj:pre-<ver>` |
| 迁移 | 首次启动幂等建表；失败停在启动阶段，不接流量 |
| 部署 | `docker compose -f docker-compose.zzy.yml up -d` |
| nginx | 备份 `/etc/nginx/sites-available/learning-assistant` → 加 `/ZZYProj/` → `nginx -t` → `reload` |
| 冒烟 | `/ZZYProj/` 200；注册/登录/发一条消息；Network 无主站 `/api/*`；主站 `/` 回归 |
| 回滚 | `docker tag zzy-proj:pre-<ver> zzy-proj:latest && docker compose up -d`；nginx 恢复备份并 reload |
| 观察 | 0–2h / 24h / 72h |
| 残余风险 | 网易 SMTP 进垃圾箱；月度 token 允许小幅超额；管理员不可读正文仅为 API 契约 |

**当前证据状态**（2026-10-22 10:02 上线）：
- 本地 G2 自动化通过：26 项 pytest + 29 项真实 HTTP 契约 smoke + 20 项 jsdom 前端接线 smoke；
- 真实邮件（A1/A22）黄：QQ `smtp.qq.com:465` 登录成功，测试验证码 API 200；待收件箱确认；
- 外部子路径（A19）黄：curl 全通，浏览器 Network 待补；
- 回滚（A20）绿：真实镜像切换，RTO 3s；
- 摘除（A21）黄：逻辑在配置副本上演练，未在生产执行。

## G3 部署证据（2026-10-22）

| 项 | 值 |
| --- | --- |
| 仓库/commit | `feat/platformify` @ `e71a8a6`，已 push `origin/main` 与 `origin/feat/platformify`（镜像构建于 `2eb13e5`，其后仅 docs） |
| 镜像 | `zzy-proj:ZZY_1.0.0` / `sha256:0b34eee964a7…`；容器 `user=1000:1000` |
| 数据 | `data/zzy/zzy.db`；备份 `zzy.db.bak_20261022`，sha256 `82377464f72736ec…` |
| nginx 备份 | `/etc/nginx/sites-available/learning-assistant.bak-20261022100235` |
| 外部 smoke | `/ZZYProj` 301；`/ZZYProj/` 200；`/ZZYProj/healthz` 200；`/ZZYProj/api/*` 返回 zzy 信封；主站 `/` 200 |
| 外部完整旅程 | 临时用户登录 → `/ZZYProj/api/chat` 真实 SSE（29 delta + done，usage 93/190）→ 清理 |
| 回滚演练 | `zzy-proj:test` → `rollback.sh pre-rollback` → 回到 `0b34eee…`；RTO 3s；外部 healthz 200；DB 未丢 |
| SMTP | QQ `smtp.qq.com:465`；测试验证码已发至 `2476318935@qq.com` |
| 管理员 | 无默认管理员；显式管理员 `1636724670@qq.com`（已提升，审计 `bootstrap_admin`） |
| SMTP 收件 | 用户已确认收到 QQ 验证码（2026-10-22） |

## 72 小时窗

- T+2h：注册/登录/发消息主旅程；容器日志无 5xx；SMTP 失败率；异常则回滚。
- T+24h：用量、错误 top、邀请/注册转化、配额触发次数。
- T+72h：关闭或回滚；每个逃逸缺陷 → 新增闸门/矩阵行，**不新增无命令清单**。

## 帽子与证据

| 闸 | 帽子 | 证据 |
| --- | --- | --- |
| G0 | 需求方 | RB + 本轮问答记录 |
| G1 | 开发 | 快闸日志 + diff |
| G2 | 验收 | AB 全绿 + 截图/DB 查询 |
| G3 | 运维 | 预检日志 + 备份校验 + 回滚演练 |
| 72h | 复盘 | 时间线 + 新闸门 |

AI 可生成证据、当红队，但**不签验收**；最终签字由 KMV 完成。
