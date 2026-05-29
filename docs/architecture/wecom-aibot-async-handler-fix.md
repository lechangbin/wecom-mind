# 企业微信长连接快速确认与后台执行修复方案

## 背景

实机测试发现，两个账号几乎同时 @ 同一个智能机器人时，企微消息读取接口能看到两条消息，但本地数据库只落入一条。已落库的那条会继续执行 Dify 并生成回复，未落库的那条没有进入 `messages_raw/messages`。

这说明问题不在 Dify 输出、不在 outbox 幂等，也不是业务去重误杀；问题发生在长连接 SDK frame 进入本地系统之前或进入 handler 时的处理方式。

## 当前问题

当前长连接 worker 的 handler 仍然在一次调用里等待完整业务链路：

```text
handler 收到 frame
-> 入库
-> 触发 Dify blocking
-> 创建 outbox
-> 发送
-> handler 返回
```

即使 Dify 被放到线程里执行，只要 handler 仍然 `await` 到 Dify 完成，对企业微信长连接 SDK 来说，这个 handler 依然没有快速返回。Dify blocking 耗时可能达到十几秒到几十秒，期间后续 frame 分发、ack 或心跳都可能受影响。

## 目标架构

长连接 handler 只做快速接收和任务提交：

```text
handler 收到 frame
-> 创建后台任务
-> 立即返回

后台任务
-> 若 frame @ 回复机器人，先用原始 req_id 发送 stream 占位响应
-> normalize
-> ingest_message 写 messages_raw / messages
-> @ 判断
-> evaluate_triggers
-> Dify blocking
-> create reply outbox
-> 用原始 req_id + 同一 stream_id 发送最终回复
-> 回写 sent / failed
```

## 边界约束

- `ingest_message()` 仍然是消息事实源入口，不能绕过入库直接调用 Dify。
- Dify 执行必须基于入库后的 `message_id/msgid/chatid/userid`，便于幂等、审计和排障。
- 后台任务必须使用独立数据库 session，不能跨线程复用 handler 所在 session。
- SQLite 写事务不能跨越 Dify blocking、企微发送、网络 ack 等外部 I/O；`trigger_event` 和 `ai_run=running` 必须先提交，再执行外部调用，避免第二条消息入库时遇到 `database is locked`。
- @ 实时回复必须绑定原始 frame 的 `req_id`，使用 `reply_stream` / `aibot_respond_msg`；主动 `send_message` / `aibot_send_msg` 只用于主动提醒等没有原始 frame 的场景。
- 同一条长连接上的最终发送动作需要串行化，避免多个发送动作同时等待 SDK ack。
- 重复 msgid 仍然必须返回 duplicated，不重复触发 Dify，不重复发送。
- 失败只写入 `ai_runs/outbox_messages/bot_replies` 状态，不在 handler 中抛出导致长连接断开。

## 实现策略

### 1. 拆分同步业务处理函数

保留 `process_incoming_aibot_frame()` 给测试和手动调用使用。新增或复用内部同步函数处理后台任务：

```text
_ingest_and_evaluate_aibot_frame()
```

它负责入库、触发、Dify 和 outbox 创建，但不负责长连接 handler 的生命周期。

### 2. Worker handler 快速返回

`WeComAiBotLongConnectionWorker._handle_frame()` 不再等待完整业务链路。它只调用 `asyncio.create_task()` 提交后台协程，然后立即返回。

后台协程再通过 `asyncio.to_thread()` 执行同步入库和 Dify blocking 逻辑。

### 3. 后台任务发送串行化

后台任务完成 outbox 创建后，进入发送阶段。发送阶段使用 worker 内部 `asyncio.Lock` 包住 `_send_trigger_outboxes()`，保证同一条 SDK 长连接不会同时发送多条最终回复。

### 3.5. 回调绑定 stream 回复

实机快发测试进一步发现，handler 快速返回后，企微消息读取接口能看到两条同账号 @ 消息，但长连接只向本地投递一条。结合 SDK 示例，@ 回复不应走主动 `send_message(chatid, body)`，而应使用原始 callback frame 的 `reply_stream(frame, stream_id, ...)`。

修复策略：

```text
@ frame 到达
-> begin_callback_stream_async(frame, "正在查询相关资料，请稍等。")
-> Dify blocking
-> create outbox
-> send_callback_final_async(outbox, frame, stream_id)
```

这样既保留 outbox 审计，又让企微侧尽早收到与该 callback req_id 绑定的响应。

### 3.6. Dify 前提交写事务

实机快发测试进一步暴露：第二条消息已经收到占位回复，但一直不替换为最终内容。日志显示第二条后台任务在入库阶段触发 `sqlite3.OperationalError: database is locked`。

根因是第一条任务创建 `trigger_event` / `ai_run=running` 后，带着未提交的 SQLite 写事务等待 Dify blocking 返回；此时第二条任务尝试写 `messages_raw/messages` 会等待锁，超过 SQLite busy timeout 后失败。

修复策略：

```text
create trigger_event
-> commit
create ai_run(status=running)
-> commit
call Dify blocking
-> update ai_run result
-> commit
create outbox / bot_reply
-> commit
send final stream
```

这样 Dify 耗时只占用业务任务时间，不占用 SQLite 写锁。

### 4. 任务生命周期管理

worker 维护后台任务集合：

```text
self._tasks: set[asyncio.Task]
```

任务完成后从集合移除。`stop()` 时取消未完成任务，避免 worker 退出后仍有孤儿任务继续使用旧连接。

## 验收标准

- handler 返回耗时不随 Dify 耗时增长。
- @ 消息能在 Dify 完成前先发出 callback-bound stream 占位响应。
- Dify blocking 运行期间，第二条 frame 仍能被 handler 接收并排入后台任务。
- 第一条 Dify blocking 运行期间，第二条消息仍能写入 `messages_raw/messages`，不会因 SQLite 写锁卡死在占位回复。
- 两个账号或同一账号几乎同时 @ 同一机器人时，两条消息都能入库。
- 两条消息分别生成 `trigger_events`、`ai_runs`、`bot_replies`、`outbox_messages`。
- 发送失败只影响对应 outbox，不阻塞其他消息入库和 Dify 执行。
- 全量测试通过。

## 后续建议

本次修复仍属于进程内后台任务，适合当前实机测试阶段。后续如果要生产化，应升级为持久化队列：

```text
长连接 handler
-> 快速入库
-> 写 message_processing_jobs
-> 独立 worker 消费 job
```

这样可以支持进程重启恢复、失败重试、任务限流和更清晰的状态机。
