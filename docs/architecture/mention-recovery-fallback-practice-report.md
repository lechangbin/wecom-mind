# @ 回复兜底恢复实践报告

## 结论

新的架构方向可行，并已按“两条链路”落到实现：补漏主动回复和 @ 回复兜底恢复不再混用。

推荐结论：

- 非 @ 主动回复继续使用 `chat_proactive_reminder`，只处理 `mentioned_bot = false` 的用户消息。
- @ 兜底不再走 `chat_proactive_reminder`，而是进入独立的 `mention_recovery` 链路。
- `mention_recovery` 复用 `group_knowledge_reply` 的 @ 回复语义、输出格式和 outbox 场景。
- 如果长连接已经发送过占位 stream，兜底链路应尽量复用原 stream 输出最终答案。
- 如果长连接完全没有收到 frame，也就不存在可复用占位；兜底只能用普通主动发送方式补一条 @ 回复。

因此，“通过消息窗口去重后判断是否为未回复或未及时回复的 @ 消息，将其改正回 @ 链路并复用占位消息”在架构上成立。当前实现已新增持久化 `wecom_reply_sessions`，使有占位的恢复场景可以复用原 callback stream。

## 本次落地结果

已完成：

- 新增 `mention_recovery` 链路，补漏窗口中的 @ 用户消息只走 `group_knowledge_reply`，不会进入 `chat_proactive_reminder`。
- 新增 `outbox_messages.scene = reply_recovery`，用于区分“补漏恢复的 @ 回复”和普通主动提醒。
- 新增 `wecom_reply_sessions`，长连接发出占位后立即保存 `frame_json`、`stream_id`、`source_msgid`、`req_id`、`chatid`、`userid` 和文本指纹。
- 正常 @ 链路发送最终答案后，会把对应 reply session 更新为 `final_status = sent`。
- 补漏恢复链路会按真实 `msgid`、`req_id` 或 `chatid + userid + content_fingerprint` 匹配 reply session；匹配成功时复用原 stream，匹配不到时保留普通发送兜底。
- 长连接 worker 的 outbox dispatcher 现在同时处理 `proactive` 和 `reply_recovery` pending outbox。

仍需实机观察：

- 企业微信 callback stream 的最长可复用时间。
- Dify 超长耗时或失败后是否需要自动标记 stalled 并做人工可见化。

## 当前实现验证

### 已具备能力

当前代码已经具备以下基础能力：

- 长连接 worker 能在收到 @ frame 后立即调用 `reply_stream(..., finish=false)` 发送占位。
- @ 消息会进入 `group_knowledge_reply`。
- `group_knowledge_reply` 成功后会创建 `scene=reply` 的 outbox。
- 最终回复可以通过同一个 `frame + stream_id` 调用 `reply_stream(..., finish=true)`。
- 补漏 worker 可以短窗口拉取历史消息、入库、再从数据库窗口触发后续逻辑。
- 数据库已有 `messages`、`trigger_events`、`ai_runs`、`outbox_messages`，可以判断一条消息是否已触发、是否正在跑 Dify、是否已发出最终回复。

### 已补齐的旧缺口

旧实现里 `callback_stream_id` 和原始 `frame` 只存在于长连接 worker 的内存任务中，补漏 worker 不能可靠复用占位。当前已通过 `wecom_reply_sessions` 补齐：占位发出后立即写入持久表，恢复链路可以通过真实 `msgid`、`req_id` 或文本指纹找回原 stream。

## 场景可行性

### 场景 A：长连接完全没有收到 @ frame

现象：

- 企业微信历史消息能拉到 @ 消息。
- 本地没有 `aibot_ws` 来源消息。
- 没有占位消息。
- 没有 `trigger_events` 或 `group_knowledge_reply`。

可行处理：

- 补漏 worker 将历史 @ 消息入库。
- `mention_recovery` 判断这是未处理 @。
- 创建或复用 `trigger_event`，调用 `group_knowledge_reply`。
- 创建 `scene=reply` outbox。
- 由于没有原始 frame 和 stream_id，只能通过 `send_message(chatid, body)` 发送普通最终回复。

结论：可恢复 @ 语义，但不能复用占位。

### 场景 B：长连接收到 @ frame，已发送占位，但后续处理失败

现象：

- 用户看到“正在查询相关资料，请稍等。”
- 本地可能没有成功入库，也可能没有 `trigger_event`。
- 历史消息补漏稍后拉到同一条 @ 消息。

可行处理：

- 长连接 worker 在发出占位后立即写入 `reply_sessions`。
- `reply_sessions` 保存 `frame_json`、`stream_id`、chatid、userid、文本指纹、时间、状态。
- 补漏 worker 入库历史 @ 消息后，`mention_recovery` 用 chatid、userid、文本指纹、短时间窗口匹配这条 session。
- 如果没有 final reply，转入 `group_knowledge_reply`。
- 最终发送时用 `reply_sessions.frame_json + stream_id` 调用 `send_callback_final_async()`。

结论：可恢复 @ 语义，也可复用占位，但依赖新增持久 reply session。

### 场景 C：长连接收到 @ frame，并且正在正常 Dify 处理中

现象：

- 已有占位。
- `trigger_event` 存在。
- `ai_run.status = running`。
- 尚未产生最终 outbox 或最终发送。

可行处理：

- `mention_recovery` 不应启动第二次 Dify。
- 只记录为 `already_processing`。
- 如果超过定义的超时时间仍未完成，再按“卡住恢复”策略处理。

结论：不能简单把“未及时回复”理解成“超过 10 秒没回复就重跑”。当前 Dify blocking 经常需要 40-60 秒，恢复阈值必须区分“尚未开始处理”和“已在处理中”。

## 推荐模块设计

### 1. Reply Session Registry

新增深一点的 Module，负责 @ 占位会话的持久化。

建议表名：

```text
wecom_reply_sessions
```

建议字段：

```text
session_id
chatid
userid
message_id nullable
source_msgid nullable
req_id nullable
content_fingerprint
frame_json
stream_id
placeholder_status
final_status
recover_status
trigger_event_id nullable
outbox_id nullable
created_at
expires_at
updated_at
last_error
```

建议唯一键：

```text
req_id
source_msgid
chatid + userid + content_fingerprint + created_at bucket
```

这个 Module 的价值是把“占位 stream 是否存在、能否复用、是否已经最终回复”集中到一个 seam，而不是散落在长连接 worker、补漏 worker、outbox dispatcher 里。

### 2. Mention Recovery

新增独立恢复链路，不复用 `chat_proactive_reminder`。

职责：

1. 从补漏窗口中找出 `mentioned_bot = true` 的用户消息。
2. 用消息窗口和 `reply_sessions` 去重。
3. 判断是否已有 `trigger_event`、`ai_run`、`reply outbox` 或最终发送。
4. 对未处理或卡住的 @ 消息，进入 `group_knowledge_reply`。
5. 如果匹配到可用 `reply_session`，用 callback stream 发最终回复。
6. 如果没有可用 `reply_session`，降级为普通群消息回复。

建议状态：

```text
no_action_already_replied
no_action_processing
recover_started
recover_sent_on_stream
recover_sent_as_message
recover_failed
```

### 3. Proactive Reply

保留当前非 @ 主动提醒链路。

关键规则：

```text
sender_type = user
mentioned_bot = false
```

它不应该再承担 @ 兜底职责。

## 推荐流程

```text
长连接收到 @ frame
  -> begin_callback_stream，占位
  -> 写入 reply_session: placeholder_sent
  -> 入库 message
  -> 创建 trigger_event
  -> 创建 ai_run: running
  -> Dify group_knowledge_reply
  -> 创建 reply outbox
  -> 用 reply_session.stream_id final stream
  -> reply_session.final_status = sent

补漏拉取消息
  -> 入库 messages
  -> @ 消息进入 mention_recovery
      -> 已回复：跳过
      -> 正在处理：跳过
      -> 占位已发但处理未开始/已失败：复用 stream 恢复
      -> 完全无占位：走 group_knowledge_reply 后普通发送
  -> 非 @ 消息进入 chat_proactive_reminder
```

## 去重与超时建议

### 去重优先级

1. 真实 `msgid`
2. `req_id`
3. `chatid + userid + content_fingerprint + create_time` 短窗口
4. `trigger_events(rule_code, message_id)`
5. `outbox_messages.idempotency_key`

### 超时建议

- `0-5 秒`：如果有占位但还没有入库，可认为长连接后台任务可能刚开始，不急于恢复。
- `5-15 秒`：如果有占位但没有 `trigger_event`，可以启动 mention recovery。
- `15-90 秒`：如果已有 `ai_run=running`，不要重跑 Dify，只标记处理中。
- `90 秒以上`：如果仍 running，可以标记 stalled，再按策略人工排查或自动恢复。

具体阈值需要结合 Dify 实际耗时调参。当前真实 Dify 经常 40-60 秒，不能用 10 秒作为“Dify 超时重跑”的判断。

## 风险与待验证点

1. **stream 可复用有效期未知**

   当前代码已经证明 40-60 秒内可以用同一 stream 输出最终答案，但企业微信允许多长时间后继续 `reply_stream(..., finish=true)` 需要实测。

   建议实测：

   - 占位后 30 秒 final。
   - 占位后 60 秒 final。
   - 占位后 120 秒 final。
   - 占位后 300 秒 final。

2. **frame_json 是否可安全持久化**

   `send_callback_final_async()` 需要原始 frame。为了恢复 stream，必须保存 frame 或保存 SDK 最小需要字段。建议先保存完整 frame，并设置短 TTL，后续再收窄字段。

3. **恢复链路不能制造重复 Dify**

   `mention_recovery` 必须先查 `trigger_events` 和 `ai_runs`。只要已有 running，就不应再跑一次。

4. **补漏不能推进 cursor 太早**

   如果 mention recovery 失败，是否推进 cursor 需要单独定义。建议补漏入库成功可以推进消息 cursor，恢复失败由 `reply_sessions/recovery_status` 继续处理，避免历史窗口反复重扫同一批消息。

## 实施建议

已按两步落地：

### 第一阶段：语义纠偏

- 非 @ 主动回复排除 `mentioned_bot = true`。
- 新增 `mention_recovery`。
- 历史拉到未处理 @ 时，走 `group_knowledge_reply`。
- 验证 @ 不再被 `chat_proactive_reminder` 抢答。

### 第二阶段：占位复用

- 新增 `wecom_reply_sessions`。
- 长连接发出占位后立即写 session。
- 正常 @ 链路 final stream 后更新 session。
- mention recovery 匹配 session 后复用 stream。
- stream TTL 仍需实机测试。

## 最终判断

这套架构比“非 @ 主动提醒兜底 @ 回复”更正确。

原因：

- @ 是显式请求，应该始终走 `group_knowledge_reply`。
- 非 @ 是主动判断，应该始终走 `chat_proactive_reminder`。
- 两者输出风格、幂等键、发送方式和用户预期都不同。
- 兜底机制应该恢复丢失的 @ 链路，而不是让主动提醒链路代答。

当前代码已经支持这个方向，并且具备“有占位则复用 stream、无占位则降级普通发送”的恢复能力。
