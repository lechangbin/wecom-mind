# Dify 双应用实机测试说明

当前运行链路只保留首批两个 Dify 应用：

- `group_knowledge_reply`：群内 @ 机器人后的知识库答疑。
- `chat_proactive_reminder`：非 @ 消息窗口扫描后的意图触发知识库主动答疑。

旧的 `reply_generation`、`intent_detection`、`conversation_segmentation`、`user_profile_analysis` 不再作为默认运行链路。

## 环境变量

```env
DIFY_CLIENT_MODE=real
DIFY_BASE_URL=http://localhost
DIFY_GROUP_KNOWLEDGE_REPLY_API_KEY=your-group-knowledge-reply-key
DIFY_CHAT_PROACTIVE_REMINDER_API_KEY=your-chat-proactive-reminder-key
DIFY_USER=wecom-bot-system
```

企微智能机器人长连接实机链路需要同时配置：

```env
WECOM_REPLY_AIBOT_ID=
WECOM_REPLY_AIBOT_SECRET=
WECOM_REPLY_AIBOT_NAME=智能机器人

# 主动提醒采集预留；当前项目还没有内置自动拉群消息调度器。
WECOM_INTENT_AIBOT_ID=
WECOM_INTENT_AIBOT_SECRET=
WECOM_BOT_ID=
WECOM_BOT_SECRET=

WECOM_SENDER_MODE=aibot_ws
```

Bot Secret 只放本地 `.env` 或部署环境变量，不写入文档、测试或提交内容。

如果先用群机器人 Webhook 发送，可以改用旧发送通道：

```env
WECOM_SENDER_MODE=webhook
WECOM_GROUP_BOT_WEBHOOK_URL=
```

## @ 答疑测试

1. 一键启动 API 服务和长连接 worker：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-services.ps1
```

2. 在测试群里 @ 回复机器人发送问题。

长连接 worker 会自动完成：

```text
receive frame -> ingest_message -> evaluate_triggers -> Dify -> outbox -> aibot_ws send_message
```

成功后会生成：

- `messages.source = aibot_ws`
- `ai_runs.workflow_code = group_knowledge_reply`
- `outbox_messages.scene = reply`
- `outbox_messages.status = sent`

仍可用手动接口调试已入库消息：

```http
POST /api/triggers/evaluate
Content-Type: application/json

{
  "message_id": 1
}
```

## 主动答疑测试

先确保同一个 `chatid` 已经有消息入库，再调用：

```http
POST /api/proactive-replies/run
Content-Type: application/json

{
  "chatid": "CHAT_ID",
  "time_range": {
    "start": "2026-05-27T00:00:00+08:00",
    "end": "2026-05-27T23:59:59+08:00"
  },
  "auto_enqueue": true
}
```

成功且 Dify 判断需要发送时会生成：

- `ai_runs.workflow_code = chat_proactive_reminder`
- `outbox_messages.scene = proactive`
- `outbox_messages.content.markdown.quote_msgid`
- `outbox_messages.status = pending`

然后调用：

```http
POST /api/outbox-messages/{outbox_id}/send
```

## 当前未自动化项

- 非 @ 消息不会即时回复，主动答疑仍需要手动或调度调用 `/api/proactive-replies/run`。
- 原意图机器人已预留为消息读取配置，但项目内尚未把 `get_msg_chat_list/get_message` 做成常驻调度器；没有入库消息时主动答疑无法判断。
- `quote_msgid` 已保存到 outbox 内容中，但当前企微发送器还没有真正调用“引用消息”能力。
