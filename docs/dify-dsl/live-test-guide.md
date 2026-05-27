# Dify 双应用实机测试说明

当前运行链路只保留首批两个 Dify 应用：

- `group_knowledge_reply`：群内 @ 机器人后的知识库答疑。
- `chat_proactive_reminder`：非 @ 消息窗口扫描后的意图触发知识库主动答疑。

旧的 `reply_generation`、`intent_detection`、`conversation_segmentation`、`user_profile_analysis` 不再作为默认运行链路。

## 环境变量

```env
DIFY_CLIENT_MODE=real
DIFY_BASE_URL=http://localhost
DIFY_GROUP_KNOWLEDGE_REPLY_API_KEY=app-zpVH2UV2yWWVHHj0zuLjOVvy
DIFY_CHAT_PROACTIVE_REMINDER_API_KEY=app-eZm0LQXA91nNaXHNu0UI6Zt8
DIFY_USER=wecom-bot-system
```

企微真实发送需要同时配置：

```env
WECOM_SENDER_MODE=app
WECOM_CORP_ID=
WECOM_AIBOT_SECRET=
WECOM_AGENT_ID=
```

如果先用群机器人 Webhook 发送，可以改用：

```env
WECOM_SENDER_MODE=webhook
WECOM_GROUP_BOT_WEBHOOK_URL=
```

## @ 答疑测试

1. 启动服务。
2. 让企微回调或手动入库一条 `mentioned_bot=true` 的消息。
3. 调用：

```http
POST /api/triggers/evaluate
Content-Type: application/json

{
  "message_id": 1
}
```

成功后会生成：

- `ai_runs.workflow_code = group_knowledge_reply`
- `outbox_messages.scene = reply`
- `outbox_messages.status = pending`

然后调用：

```http
POST /api/outbox-messages/{outbox_id}/send
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

- 企微回调入库后不会自动调用 `/api/triggers/evaluate`。
- outbox 创建后不会自动发送，需要手动调用 send 接口或后续补调度器。
- `quote_msgid` 已保存到 outbox 内容中，但当前企微发送器还没有真正调用“引用消息”能力。
