# 细节矩阵（DM）— ZZyProj 平台化改造

> DM 是生成问题的工具，不是勾选清单。填不出来的格子 = 未决问题，回到 RB 的 Q 表。

| # | 维度 | 本项目的真实答案 | 答“否”会怎样 | 最后一步证据（命令） |
| --- | --- | --- | --- | --- |
| 1 | 需求语义 | 10 条 R 均可判定；不做清单明确；Q 表仅剩运营文案/时间窗 | 验收对象漂移，上线后返工 | 让非作者按 R1/R4/R5 各复现 1 条：`pytest -q` |
| 2 | 单一事实源 | 模型白名单、模式提示词、默认额度、错误码只在 `server/config.py`；前端只发 mode id | 前端提示词与服务端漂移，同一输入两种行为 | `grep -rn "system\|提示词" site/index.html` 应无业务提示词；`grep -n "MODES\|DEFAULT_" server/config.py` |
| 3 | 时点语义 | 会话 ID 用 UUID 不复用；消息落库带 created_at；删除为硬删除；不做语义改写 | 旧会话串号、删除后“复活” | `pytest -q -k isolation` + 删除后 GET 404 |
| 4 | 接口契约 | 请求体只发对象一次（前端 `JSON.stringify` 仅在 fetch 边界）；服务端 Pydantic 校验；SSE 事件格式固定 | 4.6.2 式双重序列化 → 全线 422 | `python tools/contract_smoke.py`（真实 HTTP + 假上游，断言收到对象而非字符串） |
| 5 | 页面接线 | 登录/注册/发码/发送/停止/新建/删除/导出/退出/管理入口逐个真实点击 | 按钮没接线：接口 200、控制台零报错 | `python tools/e2e_smoke.py` 或浏览器 Network 截图（A19） |
| 6 | 渲染/文案/编码 | 静态 HTML 无模板引擎；仍需检查 `undefined`/`[object Object]`/`NaN` 不进 DOM；中文/emoji/超长输入 | 错误被渲染成 `[object Object]`，用户看不懂 | 浏览器截图 + `grep -n "object Object" site/index.html` 应为 0 |
| 7 | 身份/权限/归属 | JWT 鉴权；每个会话查询带 `user_id`；越权统一 404；管理员接口 `role=admin` | A 能读 B 的对话 | `pytest -q -k "isolation or admin"` |
| 8 | 安全默认 | Header 鉴权，无 cookie；无 CORS 中间件（同源）；密码 bcrypt；验证码哈希存储；key 只在服务端 .env | 密钥下发前端；验证码明文入库 | `pytest -q -k security`；`grep -rn "sk-\|WSPe" site server --include=*.py --include=*.html` 应无结果 |
| 9 | 配置生效 | 生产只读容器环境变量；启动打印生效配置（脱敏）；缺 `ZZY_JWT_SECRET`/`DEEPSEEK_API_KEY` 直接退出；SMTP 未配 → 注册 503 | 页面能开、提交静默失败 | 启动日志 + `curl -X POST .../auth/request-code` 断言 503 |
| 10 | 部署/迁移 | 独立镜像 tag `zzy-proj:<version>` + 保留 `zzy-proj:pre-<version>`；DB 首次启动建表（幂等）；卷 `./data/zzy`；nginx 子路径 | 重建容器丢数据；请求误打主站 | `docker compose up -d` + A19/A20 命令 |
| 11 | 失败可见性 | 统一信封 `{code,message,details,request_id}`；前端展示 message + request_id；上游 401/402/429/5xx 分别可读 | “操作失败”四个字，无法定位 | `pytest -q -k failure` + 浏览器截图（A17） |
| 12 | 回滚/恢复 | `docker tag pre-<v> latest && docker compose up -d` 一条命令；DB 先备份；nginx 配置备份 `.bak-20261022`；摘除方案 | 坏了只能重建，RTO 不可控 | G3 rehearsal 输出 + RTO 记录 |

## 本次先做哪几维

这是「老平台 + 新服务 + 账号/数据」→ **12 维全做**，重点 4/5/8/9/11。

## 已知边界（写清楚，不假装覆盖）

- 月度 token 配额检查与流结束记账之间存在竞态，允许小幅超额（≤ 单次请求 token）；日次数在调用上游前原子占位，不允许超发。
- 管理员“不可读正文”是 API/UI 契约；拥有宿主机 root 的人仍可直接读 SQLite 文件。
- 邮件投递依赖网易 SMTP 的反垃圾策略；收件方可能进垃圾箱，验收以实际收件为准。
