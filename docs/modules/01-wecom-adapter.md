# 01. 企微接入模块

## 1. 模块职责

企微接入模块负责企业微信相关的入口和出口适配。

负责：

- 通过企业微信智能机器人长连接接收实时消息。
- 将长连接 frame 标准化为 `MessageIngestRequest`。
- 复用消息入库、触发、outbox 和发送模块完成实机闭环。
- 通过长连接发送回复或主动消息。
- 保留历史消息拉取能力作为补漏方向。
- 适配智能机器人回复能力。
- 保存企微侧回执。

不负责：

- Dify 工作流调用。
- 消息语义分析。
- 会话切分。
- 用户画像。

## 2. 上下游关系

上游：

- 企业微信智能机器人长连接。
- 可选历史消息读取能力。

下游：

- 消息处理模块。
- 发送模块。
- 任务调度模块。

## 3. 核心子能力

### 3.1 AiBot Long Connection Worker

入口：

- `scripts/run_wecom_aibot_worker.py`

处理流程：

1. 使用 `WECOM_REPLY_AIBOT_ID` 和 `WECOM_REPLY_AIBOT_SECRET` 建立 WebSocket 长连接；未配置时兼容兜底到旧 `WECOM_AIBOT_*`。
2. 注册 `message.text`、`message.mixed` 等消息事件。
3. 收到 frame 后立即提交后台处理任务，handler 快速返回，避免阻塞企业微信长连接 SDK 的后续 frame 分发。
4. 如果 frame 明确 @ 回复机器人，先用原始 frame 的 `req_id` 发一段 `reply_stream(..., finish=false)` 占位响应。
5. 后台任务通过 `AiBotFrameNormalizer` 转为 `MessageIngestRequest`。
6. 后台任务调用 `ingest_message()` 写入 `messages_raw/messages`。
7. @ 消息调用 `evaluate_triggers()`；非 @ 消息在长连接实时入口仅入库，后续由补漏 worker 的数据库窗口扫描判断是否主动答复。
8. `trigger_event` 和 `ai_run=running` 写入后先提交事务，再调用 Dify blocking，避免 SQLite 写锁影响后续消息入库。
9. Dify 成功后创建 outbox。
10. @ 实时回复使用原始 frame 和同一个 `stream_id` 发最终 `reply_stream(..., finish=true)`；主动提醒使用 `send_message(chatid, body)`。
11. 写入发送回执和状态。

### 3.2 Message Puller

处理流程：

1. 历史消息补漏不是主接收链路；主接收链路仍是企业微信智能机器人长连接。
2. 补漏任务默认每 10 秒运行一次，读取最近 12 秒，或在已有 cursor 时读取 `last_pulled_at - 2s` 到当前时间。
3. 消息读取配置入口为 `WECOM_INTENT_AIBOT_*` / `WECOM_BOT_*`，该机器人应具备群消息读取权限。
4. 拉到的消息必须先转换为 `MessageIngestRequest`，并复用 `ingest_message()` 写入 `messages_raw/messages`。
5. 补漏任务不得直接把企微拉取结果传给 Dify。Dify 输入只能从数据库窗口构造。
6. 成功完成入库和主动提醒扫描后，更新 `wecom_mcp_pull_cursors.last_pulled_at`。
7. 如果 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true`，主动提醒 outbox 创建后进入自动发送链路；`aibot_ws` 模式由长连接 worker 复用回复机器人连接发送并回写 `sent/failed`。

当前代码已提供真实 MCP 消息源适配和常驻补漏 worker。补漏 worker 只有在 `WECOM_MESSAGE_RECONCILE_ENABLED=true` 且配置了 `WECOM_MESSAGE_RECONCILE_CHATIDS` 时运行。

### 3.3 AiBot Reply Adapter

负责：

- 普通回复。
- 流式回复。
- 主动发送群消息。
- 主动 @ 用户。
- 模板卡片可后置。

注意：

- @ 实时流式回复必须依赖原始 frame 或 req_id。
- 主动消息不依赖原始 frame。

## 4. 关键数据表

- `wecom_mcp_callbacks`
- `wecom_mcp_pull_cursors`
- `message_ingestion_jobs`
- `messages_raw`
- `outbox_messages`

## 5. MVP 实现范围

必须实现：

- 智能机器人长连接 worker。
- 长连接 frame 标准化。
- 长连接 handler 快速返回，Dify blocking 与发送在后台任务中完成。
- Dify blocking 前提交已写入的 `trigger_event/ai_run`，不持有 SQLite 写事务等待外部服务。
- @ 回复使用 callback-bound stream 先占位再输出最终答案。
- 文本和 mixed 消息入库。
- 消息入库时在单表 `messages` 内标记 `sender_type` 和 `bot_role`，不拆分用户消息表和机器人消息表。
- 应用启动时回填已有消息的 `sender_type` / `bot_role`，避免旧数据中的机器人消息被主动提醒误扫。
- 真实 `msgid` 跨 `source` 幂等，避免长连接和历史补漏重复写同一条消息。
- @ 消息自动触发 Dify。
- outbox 通过长连接发送。
- 历史补漏常驻 worker：短窗口拉取、幂等入库、数据库驱动主动提醒扫描、cursor 更新、可选自动发送。

优先验证：

- 真实测试群 @ 机器人可以收到回复。
- 非 @ 消息在长连接入口不会即时回复；启用补漏 worker 和 `AUTO_SEND=true` 后，可在短窗口扫描后自动主动答复。
- 重复 msgid 不会重复入库和发送。

可延后：

- 模板卡片。
- 附件下载和解密。
- 普通群机器人 webhook 备用通道。

## 6. 验收标准

- 能通过长连接接收一条企微消息并写入消息表。
- 两个账号或同一账号几乎同时 @ 同一机器人时，两条消息都能入库并分别进入后台处理。
- @ 回复在 Dify 完成前能先发送 callback-bound stream 占位响应。
- 第一条 Dify blocking 运行期间，第二条消息不会因为数据库写锁停留在占位响应。
- 重复消息不会重复创建消息。
- 能将至少一种文本消息写入消息处理模块。
- 能向指定 chatid 发送一条普通消息。
- 非 @ 消息不会在长连接入口直接调用 Dify；补漏 worker 会从数据库窗口调用 `chat_proactive_reminder`。
- 主动提醒扫描只读取用户消息，不把机器人消息作为候选问题传给 Dify。
- 历史补漏拉取的用户消息必须携带真实 `userid` 并落库到 `messages.userid`；主动回复不做无 `userid` 的身份推断。
