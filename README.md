# 基于自建系统 + Dify 的企微机器人

当前分支：企业微信智能机器人长连接实机测试版，基于 `v0.1-dify-ai-baseline` 后续更新。

## 已实现范围

阶段 1 基础设施：

- FastAPI 后端项目骨架。
- 环境变量配置读取，支持 `.env`。
- SQLAlchemy 数据库连接与健康检查。
- 统一 API 响应格式：`success`、`request_id`、`data`、`error`。
- 统一错误码与异常处理。
- 请求 ID 中间件，支持透传 `X-Request-ID`。
- 带请求 ID 的基础日志，并对 secret、token、api_key、access_token 等敏感字段脱敏。
- 可复用 JSON schema 校验工具。
- `GET /health` 健康检查接口。

阶段 2 企微接入与消息入库：

- ORM 数据表：`wecom_chats`、`wecom_users`、`wecom_mcp_callbacks`、`wecom_mcp_pull_cursors`、`message_ingestion_jobs`、`messages_raw`、`messages`。
- 应用启动时自动 `create_all` 建表，继续兼容默认 SQLite。
- 旧 `POST /api/wecom/callbacks/mcp` 仅作为兼容入口保留，不再作为当前主接收链路。
- 当前主接收链路为企业微信智能机器人长连接 Worker，收到消息后标准化为 `MessageIngestRequest` 并复用入库逻辑。
- `POST /api/wecom/messages/ingest`：保存 `messages_raw`，生成 `messages` 标准化记录。
- 文本消息标准化、quote 保存、机器人 @ 识别、群聊和用户基础信息/活跃时间更新。
- 同一 `idempotency_key` 或同一 `source + external_msgid` 重复入库时返回 `duplicated=true`，不重复写入核心表。

阶段 3 触发路由与 Dify Gateway：

- ORM 数据表：`trigger_rules`、`trigger_events`、`ai_workflows`、`ai_runs`。
- 应用启动时幂等初始化默认 mention 触发规则和 `reply_generation/v1` 工作流配置。
- `POST /api/triggers/evaluate`：读取 `messages`，对 `mentioned_bot=true` 的消息创建 `trigger_events`，同一 `message_id + rule_code` 不重复触发。
- 命中 mention 规则后创建并执行 `ai_runs`，保存 `workflow_code`、`workflow_version`、`trigger_event_id`、输入 JSON、输出 JSON、`response_mode`、状态、耗时和错误信息。
- Dify 能力通过 `DifyClient` 抽象接入；当前默认是 `MockDifyClient`，返回合法的 `reply_generation` 结构化 JSON。
- 使用 JSON schema 校验 `reply_generation` 输出：`action` 必填，`confidence` 必须在 0 到 1，`action=reply` 时 `reply.content` 不得为空。
- 非法输出会将 `ai_runs.status` 置为 `invalid_output`，不会创建发送任务。
- `GET /api/ai-runs` 和 `GET /api/ai-runs/{run_id}` 可查询运行记录。

阶段 4 发送模块：

- ORM 数据表：`bot_replies`、`outbox_messages`。
- `reply_generation` 的 `ai_runs.status=success` 且 `output_json.action=reply` 时，创建 `bot_replies` 和 `outbox_messages`。
- ai_run 生成 outbox 的业务幂等键为 `reply_{ai_run.run_id}`，重复 evaluate 同一消息不会重复创建回复或发送任务。
- `POST /api/outbox-messages`：支持手动创建发送任务，同一 `idempotency_key` 不重复创建。
- `GET /api/outbox-messages`：支持按 `status`、`chatid` 查询。
- `GET /api/outbox-messages/{outbox_id}`：查询单条发送任务。
- `POST /api/outbox-messages/{outbox_id}/send`：通过 `WeComMessageSender` 抽象发送；当前默认是 `MockWeComMessageSender`。
- mock 发送成功后，outbox 标记为 `sent`，保存 `external_msgid`、`raw_response`、`sent_at`；发送失败标记为 `failed` 并记录错误和重试次数。
- 已经 `sent` 的 outbox 再次发送会直接返回当前状态，不重复调用发送适配器。

阶段 5 智能会话切分：

- ORM 数据表：`conversation_segments`。
- 应用启动时幂等初始化 `conversation_segmentation/v1` 工作流，`response_mode=blocking`，包含输入和输出 JSON schema。
- `MockDifyClient` 已按 `workflow_code` 分支：`reply_generation` 负责回复生成，`conversation_segmentation` 基于输入消息窗口返回 mock 会话段建议。
- `POST /api/conversations/segment/run`：按 `chatid + start_time + end_time` 查询消息窗口，创建 `ai_runs`，调用 Dify client，并校验输出。
- 会话切分输出校验包括：`start_msgid/end_msgid` 必须存在于输入消息、区间不能交叉、`participants` 必须来自输入消息用户、结构必须符合 schema。
- Dify 只提供建议，最终 `conversation_no`、边界确认和写库由自建系统完成；当前编号格式为 `conv_YYYYMMDD_xxxxxx`。
- 同一 `chatid + start_msgid + end_msgid` 重复运行不会重复创建会话段，直接复用已有 `conversation_segments` 记录。
- 非法输出会将 `ai_runs.status` 置为 `invalid_output`，不会写入 `conversation_segments`。
- `GET /api/conversations` 支持按 `chatid` 查询会话段列表。
- `GET /api/conversations/{conversation_no}` 可查询单个会话段详情。

阶段 6 用户画像分析：

- ORM 数据表：`user_profiles`、`user_profile_facts`。
- 应用启动时幂等初始化 `user_profile_analysis/v1` 工作流，`response_mode=blocking`，包含输入和输出 JSON schema。
- `MockDifyClient` 已支持 `user_profile_analysis`，会基于输入 `recent_messages` 返回稳定的 mock 画像摘要和事实。
- `POST /api/profiles/analyze/run`：按 `userid + time_range` 查询用户消息，读取用户参与的会话摘要，读取当前最新画像，创建 `ai_runs`，调用 Dify client，并校验输出。
- 用户画像输出校验包括：`output.userid` 必须等于输入 `userid`，`evidence_msgids` 必须来自本次输入消息，`evidence_conversation_nos` 必须来自本次输入会话摘要，结构必须符合 schema。
- 成功时写入新的 `user_profiles` 版本；旧 active 画像会标记为 `superseded`，最新有效画像可按 `userid` 查询。
- fact 置信度规则：`confidence >= 0.75` 写入 active fact 并进入 `profile_json.facts`；`0.5 <= confidence < 0.75` 写入 low_confidence fact 但不进入核心画像；`confidence < 0.5` 仅保留在 `ai_runs.output_json`。
- 非法输出会将 `ai_runs.status` 置为 `invalid_output`，不会写入画像或事实。
- `GET /api/users/{userid}/profile` 可查询最新画像快照和事实。

阶段 7 定时意图识别与主动提醒：

- ORM 数据表：`scheduled_intents`。
- 应用启动时幂等初始化 `intent_detection/v1` 工作流，`response_mode=blocking`，包含输入和输出 JSON schema。
- 应用启动时幂等初始化默认 `default_scheduled_intent_detection` schedule 触发规则。
- `MockDifyClient` 已支持 `intent_detection`，会基于输入 `messages` 和 `known_users` 返回稳定 mock actions。
- `POST /api/scheduled-intents/run`：手动触发一个“定时扫描执行入口”，按 `chatid + time_range` 查询消息窗口，创建 `ai_runs`，调用 Dify client，并校验输出。
- 意图输出校验包括：`target_userid` 必须来自 `known_users`，`evidence_msgids` 必须来自输入消息，`confidence >= 0.7` 时 `suggested_message` 不得为空。
- `confidence < 0.7` 的结果会写入 `scheduled_intents.status=ignored_low_confidence`，不会创建 outbox。
- `confidence >= 0.7` 且 `auto_enqueue=true` 时，会写入 `scheduled_intents.status=enqueued`，并创建 `scene=proactive` 的 markdown outbox，内容包含 `<@target_userid>` 和建议消息。
- 同一 `chatid + time_range + target_userid + first_evidence_msgid` 重复执行不会重复创建 intent 或 outbox。
- `GET /api/scheduled-intents` 支持按 `chatid`、`status` 查询意图记录。

阶段 8 管理统计与查询 API：

- 新增管理统计模块，MVP 直接从现有业务表聚合，不新增统计宽表。
- `GET /api/dashboard/overview`：返回指定日期的消息、群聊、用户、触发、AI 调用、outbox、scheduled intent 等核心计数。
- `GET /api/chats`：支持 `limit/offset` 分页，返回群聊基础信息和 `message_count`。
- `GET /api/chats/{chatid}/messages`：支持 `limit/offset` 分页，返回指定群消息列表。
- `GET /api/users`：支持 `limit/offset` 分页，返回用户基础信息、`message_count` 和最新画像摘要。
- `GET /api/stats/messages`：支持 `start/end/group_by=day|hour`，返回消息趋势、类型分布、群排行和用户排行。
- `GET /api/stats/workflows`：按 `workflow_code` 聚合 AI 调用总数、成功/失败/非法输出数量、成功率和平均耗时。
- `GET /api/ai-runs` 增强了 `workflow_code`、`status`、`limit`、`offset` 查询参数，保持原详情接口不变。

阶段 9 真实 Dify HTTP 调用适配：

- `DifyClient` 保持抽象接口，默认仍使用 `MockDifyClient`，本地测试不会访问外网。
- 新增 `DifyHttpClient`，仅当 `DIFY_CLIENT_MODE=real` 时调用真实 Dify HTTP API。
- real 模式使用 blocking 调用，请求体为 `inputs`、`response_mode=blocking`、`user`。
- 支持 `POST /workflows/run`，以及配置了 `ai_workflows.dify_workflow_id` 时调用 `POST /workflows/{workflow_id}/run`。
- 兼容 `DIFY_BASE_URL` 是否已带 `/v1`，避免拼出重复 `/v1/v1`。
- 兼容 Dify blocking 响应中的 `data.outputs`、`outputs.result`、`outputs.output`，支持对象或 JSON 字符串。
- 非 2xx、请求超时、`data.status != succeeded` 会抛出 `DifyClientError`；现有业务服务会将对应 `ai_runs.status` 标记为 `failed`，不写业务结果。

阶段 10 真实企业微信出站发送适配：

- `WeComMessageSender` 保持抽象接口，默认仍使用 `MockWeComMessageSender`，本地测试不会访问真实企微网络。
- 新增 `WeComApiClient`，支持 `gettoken`、access_token 缓存、提前 5 分钟刷新、token 失效刷新重试、`errcode=-1` 简单重试。
- `WECOM_SENDER_MODE=app` 时使用 `WeComAppMessageSender`，支持 `/message/send` 应用消息和 `/appchat/send` 应用群聊消息。
- `WECOM_SENDER_MODE=webhook` 时使用 `WeComWebhookMessageSender`，直接调用群机器人 webhook，不获取 access_token。
- `WECOM_SENDER_MODE=aibot_ws` 时使用智能机器人长连接发送，建议由 worker 持有同一条长连接执行发送。
- 真实 sender 统一返回 outbox 发送模块可识别的 `success/external_msgid/raw_response/error_code/error_message` 结构。
- 发送失败不会丢失 outbox 审计信息，仍由 `send_outbox_message` 标记 `failed` 并保存错误和原始响应。

阶段 11 真实企业微信回调验签与 AES 解密：

- 新增 `GET /api/wecom/callbacks/mcp` URL 验证入口，real 模式会校验 `msg_signature/timestamp/nonce/echostr`，解密 `echostr` 并返回纯明文。
- `POST /api/wecom/callbacks/mcp` real 模式会从 XML body 提取 `<Encrypt>`，按企业微信 SHA1 规则验签后进行 AES-256-CBC 解密。
- 解密明文按 `random(16) + msg_len(4 bytes big endian) + msg + receiveid` 解析，并校验 `receiveid=WECOM_CORP_ID`。
- 解密后的 XML 会解析为 dict，写入现有 `wecom_mcp_callbacks.raw_body`，继续复用幂等、审计和入库任务创建流程。
- mock 模式保持本地 JSON 回调行为，默认测试不依赖真实企业微信。

阶段 12 消息入库任务处理器：

- 新增可手动触发的 `message_ingestion_jobs` 处理器，当前只处理 `job_type=normalize`，`pull` 任务继续保留 pending。
- `normalize` 任务会读取对应 callback 的 `raw_body`，支持 `raw_message`、`messages` 数组、raw_body 本身为 raw_message，以及阶段 11 解密后的企业微信 XML dict 文本消息。
- 转换后的消息复用现有 `ingest_message()` 写入 `messages_raw/messages`，继续沿用消息入库幂等逻辑。
- 每条转换消息使用稳定幂等键：有 `msgid` 时为 `mcp_msg_{msgid}`，否则为 `callback_{callback_id}_message_{index}`。
- 支持查询 ingestion jobs、手动执行单个 normalize job、批量执行 pending normalize jobs。
- job 状态支持 `running/succeeded/skipped/failed` 流转，并同步更新 callback 的 `processed/failed` 状态和错误信息。

阶段 14 企业微信智能机器人长连接实机链路：

- 新增 `AiBotFrameNormalizer`，将长连接 SDK frame 转成现有 `MessageIngestRequest`。
- 新增 `process_incoming_aibot_frame()`，完成 `ingest_message -> evaluate_triggers -> Dify -> outbox -> send` 的 @ 消息闭环。
- 长连接 worker 收到 frame 后快速提交后台任务并返回；入库、Dify blocking 调用和发送在后台任务中执行，避免多个账号连续 @ 时阻塞后续 frame。
- 新增 `WeComAiBotWsMessageSender`，支持 @ 回调绑定 `reply_stream(frame, stream_id, ...)` 回复，也支持主动 `send_message(chatid, body)` 推送 markdown/template_card。
- `trigger_event` 与 `ai_run=running` 写入后会先提交事务，再调用 Dify blocking，避免 SQLite 写锁导致后续 @ 消息只显示占位回复。
- 新增 `WeComAiBotLongConnectionWorker` 和 `scripts/run_wecom_aibot_worker.py`，用于本地启动长连接常驻进程。
- 支持回复机器人与意图/拉消息机器人分离：`WECOM_REPLY_AIBOT_*` 用于 @ 回复和发送，`WECOM_INTENT_AIBOT_*` / `WECOM_BOT_*` 预留给消息读取和主动提醒采集。
- 非 @ 消息默认只入库；启用 `MessageReconcileWorker` 后会按短窗口补漏，@ 漏处理消息回到 `group_knowledge_reply` 恢复链路，非 @ 消息再从数据库触发 `chat_proactive_reminder`，在 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true` 时可自动发送客服答复。

阶段 15 历史消息补漏与数据库驱动主动提醒预研实现：

- 新增补漏配置：默认 10 秒扫描周期、12 秒拉取窗口、2 秒 overlap。
- `messages` 单表增加 `sender_type` 和 `bot_role`，用于区分用户消息、回复机器人消息和意图采集机器人消息，不拆分用户/机器人消息表。
- 应用启动时会轻量回填已有 `messages` 行的 sender 分类，避免旧 SQLite 数据中机器人消息被默认当成用户消息扫描。
- 入库幂等增强：真实 `msgid` 会跨 `source` 去重，避免同一条消息先由长连接入库、再由历史补漏入库时重复写入。
- `src/app/wecom/message_reconcile.py` 提供单次补漏执行能力：计算窗口、调用注入的历史消息源、幂等入库、先执行 @ 补漏恢复，再从数据库窗口触发 `chat_proactive_reminder`，成功后更新 `wecom_mcp_pull_cursors.last_pulled_at`。
- 新增 `mention_recovery`：补漏拉到未完成的 @ 消息时，复用 `group_knowledge_reply` 并创建 `scene=reply_recovery` outbox，不再让主动提醒工作流代答 @ 问题。
- 新增 `wecom_reply_sessions`：长连接发出 callback-bound 占位后持久化 `frame_json + stream_id`，补漏恢复匹配成功时可复用原占位 stream 发最终回复。
- 新增 `WeComMcpMessageSource`，通过意图/拉消息机器人获取 msg MCP 配置，并调用 `get_message` 拉取群历史消息。
- 新增 `MessageReconcileWorker` 和 `scripts/run_message_reconcile_worker.py`，可按 `WECOM_MESSAGE_RECONCILE_CHATIDS` 常驻扫描多个群。
- `start-services.ps1` 会在 `WECOM_MESSAGE_RECONCILE_ENABLED=true` 时同时启动补漏 worker。
- 主动提醒扫描改为 SQL 时间窗口查询，只读取 `sender_type=user` 的用户消息。
- `chat_proactive_reminder` 输入会携带由 `trigger_events` 生成的 `handled_records`，避免重复回答已经由 @ 回复处理过的消息。
- `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true` 时，补漏 worker 创建 `reply_recovery` 或 `proactive` outbox；`WECOM_SENDER_MODE=aibot_ws` 下由长连接 worker 复用同一条回复机器人连接发送并回写 `sent/failed`，其他 sender 模式仍可由补漏 worker 直发。

当前仍不包含 Dify streaming、复杂权限、统计宽表、后台统计大屏和前端。历史消息补漏已具备真实 MCP 消息源适配和常驻 worker，但仍不作为主接收入口。

## 本地启动

1. 安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

2. 准备环境配置：

```powershell
Copy-Item .env.example .env
```

默认使用 SQLite：`sqlite:///./data/app.db`。如需换数据库，修改 `.env` 中的 `DATABASE_URL`。

默认 Dify 客户端为 mock：

```env
DIFY_CLIENT_MODE=mock
```

如需启用真实 Dify blocking 调用，配置：

```env
DIFY_CLIENT_MODE=real
DIFY_BASE_URL=https://api.dify.ai/v1
DIFY_API_KEY=your-dify-api-key
DIFY_TIMEOUT_SECONDS=120
DIFY_MAX_RETRIES=0
DIFY_USER=wecom-bot-system
```

`DIFY_BASE_URL` 可填写带 `/v1` 或不带 `/v1` 的地址；系统会统一拼接到 Dify API v1。real 模式缺少 `DIFY_BASE_URL` 或 `DIFY_API_KEY` 时会在构建客户端时报错。

默认企微回调验证为 mock：

```env
WECOM_MCP_VERIFY_MODE=mock
```

启用企业微信回调 real 验签和 AES 解密：

```env
WECOM_MCP_VERIFY_MODE=real
WECOM_MCP_TOKEN=your-callback-token
WECOM_MCP_ENCODING_AES_KEY=your-43-char-encoding-aes-key
WECOM_CORP_ID=your-corp-id
```

real 模式缺少 `WECOM_MCP_TOKEN`、`WECOM_MCP_ENCODING_AES_KEY` 或 `WECOM_CORP_ID` 时会在构建 verifier 时报错。URL 验证响应是纯明文；POST 回调成功响应是纯文本 `success`。

默认企微发送器为 mock：

```env
WECOM_SENDER_MODE=mock
```

启用企业微信自建应用发送：

```env
WECOM_SENDER_MODE=app
WECOM_API_BASE_URL=https://qyapi.weixin.qq.com/cgi-bin
WECOM_CORP_ID=your-corp-id
WECOM_AIBOT_SECRET=your-app-secret
WECOM_AGENT_ID=100001
WECOM_TIMEOUT_SECONDS=10
WECOM_MAX_RETRIES=2
```

`target_userids` 非空时调用 `/message/send`，无 `target_userids` 但有 `chatid` 时调用 `/appchat/send`。`/appchat/send` 要求该 `chatid` 是应用创建的群聊，MVP 不负责校验来源。

启用群机器人 webhook 发送：

```env
WECOM_SENDER_MODE=webhook
WECOM_GROUP_BOT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
WECOM_TIMEOUT_SECONDS=10
```

webhook 模式不调用 `gettoken`，text 消息会把 `target_userids` 映射为 `mentioned_list`。

启用企业微信智能机器人长连接实机链路：

```env
# 兼容兜底字段：未配置分角色机器人时使用。
WECOM_AIBOT_ID=
WECOM_AIBOT_SECRET=
WECOM_AIBOT_NAME=机器人

# @ 回复与发送使用的机器人。
WECOM_REPLY_AIBOT_ID=your-reply-bot-id
WECOM_REPLY_AIBOT_SECRET=your-reply-bot-secret
WECOM_REPLY_AIBOT_NAME=智能机器人

# 非 @ 消息读取、主动提醒采集使用的机器人；需要具备群消息读取权限。
WECOM_INTENT_AIBOT_ID=your-intent-bot-id
WECOM_INTENT_AIBOT_SECRET=your-intent-bot-secret
WECOM_INTENT_AIBOT_NAME=智能机器人
WECOM_BOT_ID=your-intent-bot-id
WECOM_BOT_SECRET=your-intent-bot-secret

# 历史消息补漏配置。自动客服实机测试只应先配置测试群。
WECOM_MESSAGE_RECONCILE_ENABLED=false
WECOM_MESSAGE_RECONCILE_CHATIDS=
WECOM_MESSAGE_RECONCILE_INTERVAL_SECONDS=10
WECOM_MESSAGE_RECONCILE_LOOKBACK_SECONDS=12
WECOM_MESSAGE_RECONCILE_OVERLAP_SECONDS=2
WECOM_MESSAGE_RECONCILE_PAGES=1
WECOM_MESSAGE_RECONCILE_AUTO_ENQUEUE=true
WECOM_MESSAGE_RECONCILE_AUTO_SEND=false
WECOM_MCP_CONFIG_ENDPOINT=https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config

WECOM_SENDER_MODE=aibot_ws
DIFY_CLIENT_MODE=real
DIFY_BASE_URL=https://api.dify.ai/v1
DIFY_GROUP_KNOWLEDGE_REPLY_API_KEY=your-group-reply-key
DIFY_CHAT_PROACTIVE_REMINDER_API_KEY=your-proactive-key
```

自动客服实机测试阶段可在本地 `.env` 将 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true`，但示例配置保持 `false` 作为安全默认。`aibot_ws` 自动发送必须同时启动长连接 worker，因为主动回复由它复用回复机器人 WebSocket 连接派发。

Bot Secret 只放本地 `.env` 或部署环境变量，不写入文档、测试或提交内容。长连接 @ 回复和主动发送使用 `WECOM_REPLY_AIBOT_*`；历史消息补漏和主动提醒采集使用 `WECOM_INTENT_AIBOT_*` / `WECOM_BOT_*`。

3. 一键启动 API 和智能机器人长连接 worker：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-services.ps1
```

默认 API 地址为 `http://127.0.0.1:8010`，日志写入 `logs/`。如需改端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-services.ps1 -Port 8000
```

实机链路为：测试群发消息 -> worker 收到 @ frame 后先发 callback-bound stream 占位 -> 入库 -> 触发 Dify -> 创建 outbox -> 用同一 stream 发送最终回复。

4. 检查健康接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
```

## 接口示例

企微 MCP 回调 MVP：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/wecom/callbacks/mcp?msg_signature=mock&timestamp=1777827600&nonce=n1" `
  -ContentType "application/json" `
  -Body '{"event_type":"message_changed","chatid":"CHAT_A","cursor":"CURSOR_1"}'
```

回调入库任务查询与手动执行：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/wecom/ingestion-jobs?status=pending&job_type=normalize&limit=20&offset=0"

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/wecom/ingestion-jobs/1/run"

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/wecom/ingestion-jobs/run" `
  -ContentType "application/json" `
  -Body '{"limit":20}'
```

批量执行入口只处理 pending normalize 任务，不会处理保留给后续真实 puller 的 pull 任务。

真实企微 URL 验证示例：

```powershell
Invoke-WebRequest `
  -Method Get `
  -Uri "http://127.0.0.1:8000/api/wecom/callbacks/mcp?msg_signature=<signature>&timestamp=<timestamp>&nonce=<nonce>&echostr=<encrypted-echostr>"
```

real 模式会返回解密后的纯文本，不包 JSON。

真实企微加密 POST 回调示例：

```powershell
Invoke-WebRequest `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/wecom/callbacks/mcp?msg_signature=<signature>&timestamp=<timestamp>&nonce=<nonce>" `
  -ContentType "application/xml" `
  -Body '<xml><ToUserName><![CDATA[wwxxxx]]></ToUserName><Encrypt><![CDATA[encrypted-payload]]></Encrypt></xml>'
```

real 模式会保存解密后的 XML dict，并返回纯文本 `success`。

内部消息入库：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/wecom/messages/ingest" `
  -ContentType "application/json" `
  -Body '{"source":"mcp","idempotency_key":"mcp_msg_MSG_1","raw_message":{"msgid":"MSG_1","chatid":"CHAT_A","chattype":"group","from":{"userid":"USER_A","name":"Alice"},"msgtype":"text","text":{"content":"@机器人 hello"},"mentioned_users":["BOT_ID"],"quote":{"msgid":"MSG_0","content":"old"},"create_time":1777827600}}'
```

构造一条 @ 消息并触发 `reply_generation`：

```powershell
$ingest = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/wecom/messages/ingest" `
  -ContentType "application/json" `
  -Body '{"source":"mcp","idempotency_key":"mcp_msg_MSG_STAGE3","raw_message":{"msgid":"MSG_STAGE3","chatid":"CHAT_A","chattype":"group","from":{"userid":"USER_A","name":"Alice"},"msgtype":"text","text":{"content":"@机器人 帮我总结一下"},"mentioned_users":["BOT_ID"],"create_time":1777827600}}'

$messageId = $ingest.data.message_id

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/triggers/evaluate" `
  -ContentType "application/json" `
  -Body (@{ message_id = $messageId } | ConvertTo-Json)

Invoke-RestMethod http://127.0.0.1:8000/api/ai-runs
Invoke-RestMethod "http://127.0.0.1:8000/api/outbox-messages?status=pending&chatid=CHAT_A"
```

Dify 边界：默认 `MockDifyClient` 不访问真实 Dify；`reply_generation` 只根据输入消息返回 `action=reply` 的结构化 JSON，`intent_detection` 只根据输入消息和 known_users 生成稳定 mock actions，`conversation_segmentation` 只根据输入消息窗口生成一段 mock 会话建议，`user_profile_analysis` 只根据输入 `recent_messages` 生成稳定 mock 画像。设置 `DIFY_CLIENT_MODE=real` 后使用 `DifyHttpClient` 调用真实 Dify blocking API；业务侧仍保留 `ai_runs` 记录和输出 schema 校验，Dify 不直接写业务库、不直接发送企微消息。

用 mock 消息跑定时意图识别并生成主动提醒 outbox：

```powershell
$messages = @(
  @{ msgid = "INTENT_MSG_1"; chatid = "CHAT_A"; userid = "USER_A"; content = "报价方案需要我确认一下。"; create_time = 1777827600 },
  @{ msgid = "INTENT_MSG_2"; chatid = "CHAT_A"; userid = "USER_B"; content = "那请今天跟进客户。"; create_time = 1777827660 }
)

foreach ($m in $messages) {
  $body = @{
    source = "mcp"
    idempotency_key = "mcp_msg_$($m.msgid)"
    raw_message = @{
      msgid = $m.msgid
      chatid = $m.chatid
      chattype = "group"
      from = @{ userid = $m.userid }
      msgtype = "text"
      text = @{ content = $m.content }
      create_time = $m.create_time
    }
  } | ConvertTo-Json -Depth 8

  Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/wecom/messages/ingest" `
    -ContentType "application/json" `
    -Body $body
}

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/scheduled-intents/run" `
  -ContentType "application/json" `
  -Body '{"chatid":"CHAT_A","job_id":"job_manual_001","time_range":{"start":"2026-05-04T00:00:00+08:00","end":"2026-05-04T02:00:00+08:00"},"auto_enqueue":true}'

Invoke-RestMethod "http://127.0.0.1:8000/api/scheduled-intents?chatid=CHAT_A&status=enqueued"
Invoke-RestMethod "http://127.0.0.1:8000/api/outbox-messages?status=pending&chatid=CHAT_A"
```

intent_detection 的 mock 边界：当前不会调用真实 Dify，也没有真实定时器；只提供可由 API 手动触发的扫描入口。Dify 输出只作为建议，自建系统负责 target/evidence/reply_instruction 校验、幂等、`scheduled_intents` 写库和 proactive outbox 创建。

用几条 mock 消息跑会话切分：

```powershell
$messages = @(
  @{ msgid = "MSG_1"; chatid = "CHAT_A"; userid = "USER_A"; content = "今天先看需求"; create_time = 1777827600 },
  @{ msgid = "MSG_2"; chatid = "CHAT_A"; userid = "USER_B"; content = "我补一下边界"; create_time = 1777827660 },
  @{ msgid = "MSG_3"; chatid = "CHAT_A"; userid = "USER_A"; content = "那就按这个窗口切分"; create_time = 1777827720 }
)

foreach ($m in $messages) {
  $body = @{
    source = "mcp"
    idempotency_key = "mcp_msg_$($m.msgid)"
    raw_message = @{
      msgid = $m.msgid
      chatid = $m.chatid
      chattype = "group"
      from = @{ userid = $m.userid }
      msgtype = "text"
      text = @{ content = $m.content }
      create_time = $m.create_time
    }
  } | ConvertTo-Json -Depth 8

  Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/wecom/messages/ingest" `
    -ContentType "application/json" `
    -Body $body
}

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/conversations/segment/run" `
  -ContentType "application/json" `
  -Body '{"chatid":"CHAT_A","start_time":"2026-05-04T00:00:00+08:00","end_time":"2026-05-04T02:00:00+08:00","mode":"auto"}'

Invoke-RestMethod "http://127.0.0.1:8000/api/conversations?chatid=CHAT_A"
```

conversation_segmentation 的 mock 边界：当前不会调用真实 Dify，不做复杂人工修正和多轮边界推理，只返回覆盖输入窗口的单段建议；系统仍会校验边界、参与者和区间合法性，并由自建系统生成 `conversation_no` 后写库。

用 mock 消息跑用户画像分析：

```powershell
$messages = @(
  @{ msgid = "PROFILE_MSG_1"; chatid = "CHAT_A"; userid = "USER_A"; content = "这个报价和折扣策略怎么定？"; create_time = 1777827600 },
  @{ msgid = "PROFILE_MSG_2"; chatid = "CHAT_A"; userid = "USER_A"; content = "客户跟进和交付排期今天确认。"; create_time = 1777827660 }
)

foreach ($m in $messages) {
  $body = @{
    source = "mcp"
    idempotency_key = "mcp_msg_$($m.msgid)"
    raw_message = @{
      msgid = $m.msgid
      chatid = $m.chatid
      chattype = "group"
      from = @{ userid = $m.userid }
      msgtype = "text"
      text = @{ content = $m.content }
      create_time = $m.create_time
    }
  } | ConvertTo-Json -Depth 8

  Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/wecom/messages/ingest" `
    -ContentType "application/json" `
    -Body $body
}

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/profiles/analyze/run" `
  -ContentType "application/json" `
  -Body '{"userid":"USER_A","mode":"incremental","time_range":{"start":"2026-05-04T00:00:00+08:00","end":"2026-05-04T02:00:00+08:00"}}'

Invoke-RestMethod "http://127.0.0.1:8000/api/users/USER_A/profile"
```

user_profile_analysis 的 mock 边界：当前不会调用真实 Dify，不做画像冲突合并、事实过期和人工审核；Dify 输出只作为建议，自建系统负责 userid/evidence/置信度规则校验、版本号生成和写库。

手动创建并发送 outbox：

```powershell
$outbox = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/outbox-messages" `
  -ContentType "application/json" `
  -Body '{"scene":"proactive","chatid":"CHAT_A","target_userids":["USER_A"],"msgtype":"text","content":{"text":{"content":"manual hello"}},"source_type":"manual","source_id":"manual_1","idempotency_key":"manual_1"}'

$outboxId = $outbox.data.outbox_id

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/outbox-messages/$outboxId/send"
```

企微发送边界：默认 `MockWeComMessageSender` 不会调用真实企业微信接口，只返回 `external_msgid=mock_msg_xxx` 和 `{ "errcode": 0, "errmsg": "ok" }` 形式的 mock 回执。设置 `WECOM_SENDER_MODE=app` 后可通过自建应用调用 `/message/send` 或 `/appchat/send`；设置 `WECOM_SENDER_MODE=webhook` 后可通过群机器人 webhook 发送。无论哪种模式，都保留 `WeComMessageSender` 接口、先写 outbox 的流程、幂等保护和状态更新。

管理统计查询：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/dashboard/overview?date=2026-05-04"
Invoke-RestMethod "http://127.0.0.1:8000/api/chats?limit=20&offset=0"
Invoke-RestMethod "http://127.0.0.1:8000/api/chats/CHAT_A/messages?limit=20&offset=0"
Invoke-RestMethod "http://127.0.0.1:8000/api/users?limit=20&offset=0"
Invoke-RestMethod "http://127.0.0.1:8000/api/stats/messages?start=2026-05-04T00:00:00%2B08:00&end=2026-05-04T23:59:59%2B08:00&group_by=hour"
Invoke-RestMethod "http://127.0.0.1:8000/api/stats/workflows?start=2026-05-04T00:00:00%2B08:00&end=2026-05-04T23:59:59%2B08:00"
Invoke-RestMethod "http://127.0.0.1:8000/api/ai-runs?workflow_code=reply_generation&status=success&limit=20&offset=0"
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

## 目录结构

```text
src/
  app/
    admin_analytics/
      schemas.py
      services.py
    api/
      admin_analytics.py
      ai_runs.py
      conversations.py
      health.py
      outbox.py
      profiles.py
      scheduled_intents.py
      triggers.py
      wecom.py
    config/
      settings.py
    core/
      errors.py
      logging.py
      request_context.py
      responses.py
      schema_validator.py
    db/
      connection.py
      defaults.py
      models.py
      session.py
    dify/
      client.py
      schemas.py
      services.py
    conversations/
      schemas.py
      services.py
    outbound/
      schemas.py
      sender.py
      services.py
    profiles/
      schemas.py
      services.py
    scheduled_intents/
      schemas.py
      services.py
    triggers/
      schemas.py
      services.py
    wecom/
      client.py
      crypto.py
      jobs.py
      schemas.py
      services.py
      verifier.py
    main.py
tests/
  test_foundation.py
  test_stage3_triggers_dify.py
  test_stage4_outbound.py
  test_stage5_conversations.py
  test_stage6_user_profiles.py
  test_stage7_scheduled_intents.py
  test_stage8_admin_analytics.py
  test_stage9_dify_http_client.py
  test_stage10_wecom_sender.py
  test_stage11_wecom_callback_crypto.py
  test_stage12_ingestion_jobs.py
  test_wecom_stage2.py
```

## 下一阶段怎么继续

当前主线以 [版本路线图](./docs/architecture/version-roadmap.md) 为准。先完成 `v0.2.1` P0 消息系统架构修复：@ 请求认领、跨来源消息归并、AI 执行状态判断、占位 stream 生命周期保护；实机回归作为该阶段验收。随后进入 `v0.3` 必需功能模块：会话沉淀/会话摘要、用户画像自动更新、第一版前端界面。Dify streaming 只考虑 @ 实时回复；非 @ 主动回复继续 blocking。Redis、持久队列、分布式锁和复杂频控等生产化优化后置，等前端使用或真实流量暴露性能瓶颈后再做。
