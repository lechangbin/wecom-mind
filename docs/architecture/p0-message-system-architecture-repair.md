# P0 消息系统架构修复计划

## 结论

本计划不是扩展功能，而是当前消息系统达到稳定实机可用所必需的架构修复。

当前系统已经能完成企业微信长连接接收、Dify blocking 调用、outbox 发送、历史消息补漏和非 @ 主动回复。但快速连续 @ 实测暴露出一个结构性缺陷：实时长连接链路和补漏链路都可以认领同一条 @ 请求，导致重复 Dify、占位 stream 绑定错乱、兜底回复重复发送。

因此，本阶段不建议重写项目，也不优先引入 Redis、持久队列或分布式锁。应先在现有 FastAPI 项目内加深几个核心 Module，让单机消息链路的状态语义正确。

## 当前问题

### 1. 没有统一的 @ 请求认领

长连接 worker 收到 @ 后会立即创建占位并开始后台 Dify。补漏 worker 每 10 秒从历史消息窗口扫描，发现同一条 @ 尚未产生 outbox 时，也会启动 `mention_recovery`。

在 Dify running 但 outbox 尚未创建的窗口内，补漏会误判为未回复，从而重复处理。

### 2. 跨来源消息无法可靠归并

同一条企微消息可能同时来自：

- `aibot_ws`
- `wecom_reconcile`

如果历史拉取没有返回与长连接相同的真实 `msgid`，系统会落出两条 `messages`。后续 `trigger_events` 按 `message_id` 去重，无法阻止同一业务 @ 请求被重复触发。

### 3. AI 执行状态分散

当前状态分散在：

- `trigger_events`
- `ai_runs`
- `outbox_messages`
- `wecom_reply_sessions`

调用方需要自己拼表判断一条 @ 是否正在处理、已完成、失败或可恢复。这个 Interface 太浅，导致补漏链路只看 outbox 时遗漏 `ai_run=running` 状态。

### 4. 占位 stream 生命周期约束不足

`wecom_reply_sessions` 已保存 `frame_json + stream_id`，但还没有足够强的生命周期约束：

- 一个占位 stream 是否只能绑定一个业务请求。
- 一个已完成的 session 是否禁止被 recovery 覆盖。
- 一个 session 是否只能绑定一个 outbox。
- recovery 匹配到已 sent session 时应跳过还是降级普通发送。

这些规则如果散落在调用方，会继续产生串占位和重复完成。

## 必须修复的 Module

### 1. Mention Request Claim Module

定位：P0，必须做。

目标：把“同一条 @ 请求只能有一个处理者”做成深 Module。

Interface 需要表达：

- claim：尝试认领一条 @ 请求。
- already_processing：已有实时链路或补漏链路正在处理。
- already_completed：已有最终回复。
- recoverable：原处理失败或超时，可以由补漏恢复。
- stalled：超过阈值仍未完成，需要排障或恢复。

长连接 worker 和补漏 worker 都必须先经过该 Interface。它不应只依赖 `message_id`，而要使用业务请求身份。

最小身份规则：

```text
priority 1: 真实 msgid
priority 2: req_id
priority 3: chatid + userid + canonical_content + create_time bucket
```

验收：

- 快速连续三条不同 @，应分别形成三个 claim。
- 同一条 @ 被长连接和补漏同时看到，只允许一个 claim 进入 Dify。
- 补漏看到 `ai_run=running` 的请求时不得重跑 Dify。

### 2. Message Identity Module

定位：P0，必须做。

目标：把跨来源消息归并从 `ingest_message()` 的局部判断提升为独立 Module。

它负责判断历史拉取消息与长连接消息之间的关系：

- new_message：新消息。
- same_platform_message：真实 `msgid` 相同。
- same_business_message：`chatid + userid + 内容 + 时间` 足够接近。
- ambiguous：疑似重复但无法安全归并，应记录排障信息，不自动触发回复。

验收：

- 有相同真实 `msgid` 时跨 source 不重复入库。
- 没有真实 `msgid` 但同用户同内容短时间邻近时，不重复触发 @。
- 两条用户快速发送的不同问题不能误合并。

### 3. AI Reply Execution State Module

定位：P0，必须做。

目标：把 `TriggerEvent -> AiRun -> Outbox -> Send` 的状态作为一个深 Module 暴露。

Interface 需要能回答：

- 当前 @ 请求是否已经创建 trigger。
- 是否已经有 running 的 Dify 调用。
- 是否已经有成功输出。
- 是否已经创建 outbox。
- 是否已经发送。
- 是否失败、可重试或已超时。

补漏链路不能自己拼表判断，也不能只看 outbox。

建议状态：

```text
claimed
running
succeeded
outbox_pending
sending
completed
failed
stalled
```

验收：

- `ai_run=running` 时，补漏跳过。
- `outbox=pending/sending/sent` 时，补漏跳过。
- `failed/stalled` 只有达到恢复阈值后才允许 recovery。

### 4. Reply Session Lifecycle Module

定位：P0 最小版必须做，完整版可后置。

目标：保护 callback-bound 占位 stream 的归属。

最小 Interface：

- create_placeholder_session(frame, stream_id)
- attach_request(session, mention_request)
- attach_outbox(session, outbox)
- mark_final_sent(session, outbox)
- can_reuse_for_recovery(session, mention_request)

最小规则：

- 一个 session 只能绑定一个 mention request。
- 一个 session 只能绑定一个 outbox。
- `final_status=sent` 后禁止被新 outbox 覆盖。
- recovery 只能复用 `final_status=pending` 且身份匹配的 session。
- 匹配不确定时宁可降级普通发送，不可串占位。

验收：

- 第二条 @ 的 Dify 先返回时，不会写入第一条 @ 的 stream。
- 已 sent session 不会被补漏 outbox 覆盖。
- 没有 session 时，recovery 可降级普通群消息，但不能重复占用别人的占位。

## 可后置内容

以下不是当前完成消息系统的前置条件，应放入后续更新：

- Redis 近期已处理集合。
- 持久化任务队列。
- 分布式锁。
- 多实例部署。
- 复杂频控。
- 自动重试策略。
- Dify streaming。
- 大规模前端可观测性。
- Message Reconcile Worker 大拆分。
- Outbox Dispatch Policy 完整版。

当前阶段只需要给 outbox 派发补一条最小规则：发送前二次检查 mention request 是否已经 completed。如果 completed，则跳过或取消 recovery outbox。

## 推荐实施顺序

### Phase 1：冻结业务请求身份

新增 Mention Request Claim 与 Message Identity 的设计文档和测试样例。

先不用大改 worker，先用离线测试固定以下场景：

- 长连接和补漏看到同一条 @。
- 补漏没有真实 msgid。
- 同用户三条不同 @ 快速发送。
- 同用户相同内容重复发送。

### Phase 2：接入长连接实时链路

长连接收到 @ 后：

```text
begin placeholder
claim mention request
bind reply session
ingest message
create ai execution
run Dify
create outbox
final send
complete mention request
```

如果 claim 返回 `already_processing/completed`，实时链路不再重复 Dify。

### Phase 3：接入补漏恢复链路

补漏入库后，对 @ 消息：

```text
resolve message identity
claim or inspect mention request
if running/completed: skip
if recoverable: run group_knowledge_reply
if session reusable: final stream
else: normal send
```

补漏不能只用 outbox 判断是否处理过。

### Phase 4：发送前二次检查

在发送 `reply_recovery` 前检查 mention request：

- 如果 completed：取消或跳过。
- 如果 running 且未超时：跳过。
- 如果当前 outbox 是 request 的 owner：允许发送。

### Phase 5：实机回归

必须覆盖：

- 同账号三条 @ 快速发送。
- 两个账号几乎同时 @。
- Dify 第二条先返回。
- 补漏 worker 正好扫描到 running 窗口。
- 历史消息没有真实 msgid。
- Dify 返回 out_of_scope。
- 一条 @ 完成后补漏再次扫到，不重复回复。

## 不重写项目的理由

当前问题不来自 FastAPI 或 Python 语言本身，而来自消息系统缺少深 Module 来承载业务状态。

即使改为 TypeScript 或 Java，如果没有 Mention Request Claim、Message Identity 和 AI Reply Execution State，同样会出现：

- 重复 Dify。
- 补漏抢答。
- 占位 stream 串线。
- outbox 重复发送。

因此本阶段应做局部架构加固，而不是全项目重写。

## 成功标准

完成本计划后，当前消息系统应满足：

- 一条业务 @ 请求最多进入一次 Dify。
- 一条业务 @ 请求最多发送一次最终回复。
- 补漏不会抢占正在运行的实时 @。
- 补漏只恢复真正失败、遗漏或超时的 @。
- 占位 stream 不会被错误 outbox 覆盖。
- 快速连续 @ 时，回复内容与对应用户问题一一对应。
