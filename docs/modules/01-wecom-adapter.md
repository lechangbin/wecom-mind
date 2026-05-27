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

1. 使用 `WECOM_AIBOT_ID` 和 `WECOM_AIBOT_SECRET` 建立 WebSocket 长连接。
2. 注册 `message.text`、`message.mixed` 等消息事件。
3. 收到 frame 后通过 `AiBotFrameNormalizer` 转为 `MessageIngestRequest`。
4. 调用 `ingest_message()` 写入 `messages_raw/messages`。
5. @ 消息调用 `evaluate_triggers()`，非 @ 消息仅入库。
6. Dify 成功后创建 outbox。
7. 使用同一条长连接发送 outbox。
8. 写入发送回执和状态。

### 3.2 Message Puller

处理流程：

1. 作为补漏能力读取指定 `chatid + time_range`。
2. 调用消息读取接口拉取最近 7 天内消息。
3. 将消息逐条转换为 `MessageIngestRequest`。
4. 复用 `ingest_message()` 幂等入库。
5. 保存游标或最后拉取时间。

### 3.3 AiBot Reply Adapter

负责：

- 普通回复。
- 流式回复。
- 主动发送群消息。
- 主动 @ 用户。
- 模板卡片可后置。

注意：

- @ 实时流式回复优先依赖原始 frame 或 req_id。
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
- 文本和 mixed 消息入库。
- @ 消息自动触发 Dify。
- outbox 通过长连接发送。

优先验证：

- 真实测试群 @ 机器人可以收到回复。
- 非 @ 消息不会被即时回复。
- 重复 msgid 不会重复入库和发送。

可延后：

- 模板卡片。
- 附件下载和解密。
- 普通群机器人 webhook 备用通道。

## 6. 验收标准

- 能通过长连接接收一条企微消息并写入消息表。
- 重复消息不会重复创建消息。
- 能将至少一种文本消息写入消息处理模块。
- 能向指定 chatid 发送一条普通消息。
- 非 @ 消息不会调用 Dify。
