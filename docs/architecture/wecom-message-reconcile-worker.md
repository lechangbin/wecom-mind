# 企微历史消息补漏与主动提醒调度架构

## 目标

本阶段把已有的单次补漏能力扩展为可长期运行的自动链路：

```text
企微历史消息源 -> 短窗口补漏 -> 幂等入库 -> @ 补漏恢复 / 非 @ 主动提醒 -> outbox -> 可选自动发送
```

它不替代企业微信智能机器人长连接。长连接仍是实时 @ 回复主链路；补漏 worker 负责两件事：企业微信侧偶发漏投递时的兜底入库与 @ 恢复，以及非 @ 消息的主动提醒判断。

## 模块边界

### 长连接 Worker

负责实时消息：

- 接收智能机器人长连接 frame。
- 将 frame 标准化并写入 `messages_raw/messages`。
- 对 @ 回复机器人消息触发 `group_knowledge_reply`。
- 使用原始 frame 绑定的 stream 发送实时回复。

### Message Reconcile Worker

负责补漏和主动提醒：

- 按配置的 `chatid` 列表循环运行。
- 每 10 秒执行一次。
- 每次读取 `last_pulled_at - 2s` 到当前时间；没有 cursor 时读取最近 12 秒。
- 调用真实企微历史消息源读取消息。
- 先写入数据库，再从数据库窗口构造 Dify 输入。
- 只扫描 `sender_type = "user"` 且 `mentioned_bot = false` 的消息。
- 对 `mentioned_bot = true` 且尚未产生回复 outbox 的用户消息，先进入 `mention_recovery`，复用 `group_knowledge_reply` 恢复 @ 语义。
- 如果长连接已经发出 callback-bound 占位，补漏恢复会通过 `wecom_reply_sessions.frame_json + stream_id` 复用原占位流发送最终答案。
- 使用 `trigger_events` 生成 `handled_records`，避免重复处理已由 @ 回复处理的消息。
- 如果历史拉取缺少真实 `msgid`，导致同一条 @ 消息在长连接和补漏侧形成不同 `external_msgid`，系统会用已存在的 mention trigger 事实、同群、同用户、同内容和短时间邻近来补充 `handled_records`；这不是文本前缀排除，而是跨来源已处理事实对齐。
- 成功后更新 `wecom_mcp_pull_cursors.last_pulled_at`。

### WeCom History Message Source

负责企微消息读取适配：

- 使用意图/拉消息机器人配置：`WECOM_INTENT_AIBOT_*`，兼容 `WECOM_BOT_*` 和旧 `WECOM_AIBOT_*`。
- 通过 `get_msg_chat_list` / `get_message` 能力读取最近 7 天内消息。
- 对外只暴露 `fetch_messages(chatid, start_time, end_time)`。
- 返回的数据必须转为 `ingest_message()` 可接受的 raw message 格式。
- `userid` 是自动主动回复的必要字段。当前实测 `get_message` 可以返回 `userid`，本阶段将它作为正式输入契约。
- `userid` 只从企微返回的 `userid`、`from_userid` 或 `from.userid` 提取。不能从文本、昵称或 msgid 反推，更不能编造。
- 如果历史消息读取没有返回真实 `userid`，这条消息不能创建主动 @ 回复 outbox；这属于平台契约不满足，不在本分支做身份推断兜底。

### Dify 与发送边界

Dify 不直接接收企微拉取结果。Dify 只接收数据库整理后的 payload：

- `messages`
- `members`
- `handled_records`
- 当前触发消息和问题摘要

主动提醒发送分两级开关：

- `auto_enqueue`：是否创建 proactive outbox。
- `auto_send`：是否创建后自动发送。

实机早期建议只在测试群打开 `auto_send`。如果 `auto_send=false`，系统只创建 pending outbox，后续由人工或 outbox worker 发送；如果 `auto_send=true`，补漏 worker 会创建 proactive outbox。`WECOM_SENDER_MODE=aibot_ws` 时，长连接 worker 复用当前回复机器人 WebSocket 连接派发新建 proactive outbox；其他 sender 模式可由补漏 worker 直发并回写 `sent/failed`。

占位与流式边界：

- @ 实时回复可以提前发送占位 stream，因为长连接 worker 持有原始 callback frame。
- 长连接发出占位后会持久化 `wecom_reply_sessions`，保存短期可复用的 `frame_json`、`stream_id`、消息指纹和最终状态。
- 补漏拉到未完成的 @ 消息时，如果能匹配到 `wecom_reply_sessions`，可以复用原 callback stream 发最终回复；完全未收到长连接 frame 的 @ 消息只能降级为普通群消息回复。
- 非 @ 主动提醒来自历史消息读取，没有原始 callback frame，不能做同一种可替换的占位 stream。
- 补漏主动回复默认保持 blocking：Dify 完成后创建 outbox，再由发送链路一次性推送。
- 如果未来要在主动提醒前先发“正在处理”类消息，它只是单独群消息，不是流式占位，必须作为产品策略单独评审。

## 数据与幂等

继续使用一张 `messages` 表保存完整时间线，不拆用户表和机器人表。

关键字段：

```text
messages.sender_type = user | bot | system
messages.bot_role = reply_bot | intent_bot | unknown_bot | null
messages.business_identity_key = chatid + userid + canonical_content + 5s bucket
messages.canonical_message_id = same business message primary id
mention_requests.business_identity_key = unique @ request claim key
```

补漏与长连接可能读取到同一条企微消息。当前 P0 策略是保留跨 source 记录，并用 `business_identity_key/canonical_message_id` 关联；同 source 的重复幂等键或重复 `external_msgid` 仍不重复入库。@ 补漏必须先检查 `mention_requests`，如果已有 `running/completed/outbox_pending/sending/failed/stalled`，不得重跑 Dify。

cursor 规则：

```text
cursor_type = message_reconcile
last_pulled_at 只在“拉取、入库、@ 恢复扫描、主动提醒扫描”全部成功后更新
```

如果中途失败，不推进 cursor。下一轮会重新读取重叠窗口，靠幂等避免重复写入和重复发送。

## 运行拓扑

```text
start-services.ps1
  ├─ API: uvicorn app.main:app
  ├─ WeCom AiBot Long Connection Worker
  └─ Message Reconcile Worker，启用条件：WECOM_MESSAGE_RECONCILE_ENABLED=true
```

worker 每轮执行：

```text
for chatid in configured_chatids:
  run_message_reconcile_once(
    chatid,
    message_source=WeComMcpMessageSource,
    dify_client=DifyHttpClient or MockDifyClient,
    sender=WeCom sender,
    auto_enqueue=config,
    auto_send=config
  )
sleep(interval_seconds)
```

## MVP 配置

```env
WECOM_MESSAGE_RECONCILE_ENABLED=false
WECOM_MESSAGE_RECONCILE_CHATIDS=
WECOM_MESSAGE_RECONCILE_INTERVAL_SECONDS=10
WECOM_MESSAGE_RECONCILE_LOOKBACK_SECONDS=12
WECOM_MESSAGE_RECONCILE_OVERLAP_SECONDS=2
WECOM_MESSAGE_RECONCILE_PAGES=1
WECOM_MESSAGE_RECONCILE_AUTO_ENQUEUE=true
WECOM_MESSAGE_RECONCILE_AUTO_SEND=false
WECOM_MESSAGE_IDENTITY_BUCKET_SECONDS=5
WECOM_MENTION_REQUEST_STALLED_AFTER_SECONDS=180
WECOM_MCP_CONFIG_ENDPOINT=https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config
```

自动客服实机测试阶段，本地 `.env` 应将 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true`，并仅配置测试群 `chatid`。`.env.example` 仍保留 `false` 作为安全默认。

`WECOM_MESSAGE_RECONCILE_CHATIDS` 使用英文逗号分隔多个群：

```env
WECOM_MESSAGE_RECONCILE_CHATIDS=CHAT_A,CHAT_B
```

## 验收口径

- 启用 worker 后，每 10 秒按群短窗口拉取消息。
- 拉到的消息先入库，再触发主动提醒扫描。
- 长连接已入库的同一业务消息被补漏再次看到时，会保留补漏来源记录并关联到同一 `canonical_message_id`，但不会重复触发 Dify。
- 补漏扫到 `mention_requests.status=running` 的 @ 请求时必须跳过，不创建 `reply_recovery` outbox。
- Dify running 超过 `WECOM_MENTION_REQUEST_STALLED_AFTER_SECONDS` 时标记 `stalled`，只记录和阻断重复处理，不自动重跑。
- 实机补漏拉取的用户消息必须能落库为非空 `messages.userid`。
- 补漏拉到未完成的 @ 消息必须进入 `mention_recovery -> group_knowledge_reply`，不得进入 `chat_proactive_reminder` 主动提醒候选窗口。
- 已有占位 session 的补漏 @ 回复应优先复用原 callback stream；无占位 session 时允许降级为普通群消息回复。
- Dify 不能输出不存在于 `members` 的 `target_userids`。
- Dify 不能引用已在 `handled_records` 中的 `quote_msgid` 创建主动回复。
- 机器人消息不会进入主动提醒候选窗口。
- 已由 @ 回复处理的消息会作为 `handled_records` 传给 Dify。
- `auto_send=false` 时只创建 pending outbox。
- `auto_send=true` 时创建 outbox 后进入自动发送链路；`aibot_ws` 由长连接 worker 发送，非 `aibot_ws` 由补漏 worker 使用 sender 发送，并将 outbox 标记为 `sent` 或 `failed`。
- 失败时不推进 cursor；下一轮靠重叠窗口和幂等恢复。
- 非 @ 主动提醒不会提前发 callback-bound 占位 stream；只有 @ 长连接回复允许占位和后续流式升级。

## 后续预排

- P0：收口 `userid` 必需契约验收，实机确认 `WeComMcpMessageSource -> messages.userid` 全链路非空。
- P0：完善 proactive outbox 失败后的人工可见性；真正自动重试需先有频控策略。
- P1：将 @ 实时回复从 blocking Dify + 占位 stream 升级为 Dify streaming；主动提醒仍保持 blocking。
- P1：补一个交互状态视图，定位消息卡在入库、Dify、outbox 还是发送。
- 多进程 per-chat 分布式锁。
- Redis 近期已处理集合。
- 严格任务队列和失败重试表。
- 长窗口历史回填。
- 复杂频控策略和主动提醒灰度策略。
