# 接口契约设计

## 1. 设计原则

接口分为四类：

1. 企微回调接口：对外暴露给企业微信。
2. 系统内部接口：服务之间调用或后台任务调用。
3. Dify 调用契约：自建系统传给 Dify 的输入 JSON 和期望返回 JSON。
4. 管理后台接口：后续前端查询和统计使用。

所有写接口必须具备：

- 幂等键。
- 状态返回。
- 错误码。
- 审计记录。

新版 Dify 契约采用：

- `intent_detection`：识别意图和动作，不输出最终回复正文。
- `reply_generation`：唯一输出可发送回复正文。
- `conversation_segmentation`：输出会话切分建议。
- `user_profile_analysis`：输出画像生成与更新建议。

## 2. 通用响应格式

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req_20260514_000001"
}
```

错误响应：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "invalid chatid",
  "details": {},
  "request_id": "req_20260514_000002"
}
```

## 3. 企微回调接口

### 3.1 接收企微 MCP/webhook 回调

`POST /api/wecom/callbacks/mcp`

用途：

- 接收企微 MCP 或 webhook 回调。
- 保存原始请求。
- 触发消息入库或消息拉取任务。

请求来源：

- 企业微信。

Query 参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| msg_signature | 是 | 企微签名，具体以企微 MCP 文档为准 |
| timestamp | 是 | 时间戳 |
| nonce | 是 | 随机数 |

Body：

```json
{
  "event_type": "message_changed",
  "chatid": "CHATID",
  "cursor": "CURSOR",
  "messages": []
}
```

响应：

```json
{
  "code": "OK",
  "message": "received",
  "data": {
    "callback_id": "cb_20260514_000001",
    "accepted": true
  },
  "request_id": "req_20260514_000001"
}
```

处理规则：

- 签名失败返回 403。
- 重复回调根据幂等键直接返回成功。
- 不在回调线程调用 Dify。

## 4. 消息处理接口

### 4.1 创建消息拉取任务

`POST /api/wecom/message-pull-jobs`

请求：

```json
{
  "chatid": "CHATID",
  "reason": "mcp_callback",
  "callback_id": "cb_20260514_000001",
  "cursor": "CURSOR",
  "time_range": {
    "start": "2026-05-14T00:00:00+08:00",
    "end": "2026-05-14T00:10:00+08:00"
  },
  "idempotency_key": "pull_CHATID_CURSOR"
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "job_id": 10001,
    "status": "pending"
  },
  "request_id": "req_20260514_000003"
}
```

### 4.2 内部消息入库

`POST /api/wecom/messages/ingest`

请求：

```json
{
  "source": "mcp",
  "idempotency_key": "mcp_msg_xxx",
  "raw_message": {
    "msgid": "MSGID",
    "chatid": "CHATID",
    "chattype": "group",
    "from": {
      "userid": "zhangsan"
    },
    "msgtype": "text",
    "text": {
      "content": "@机器人 帮我总结一下"
    },
    "quote": null,
    "create_time": 1778716800
  }
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "raw_message_id": 20001,
    "message_id": 30001,
    "duplicated": false
  },
  "request_id": "req_20260514_000004"
}
```

## 5. 触发与 AI 接口

### 5.1 评估触发规则

`POST /api/triggers/evaluate`

请求：

```json
{
  "message_id": 30001,
  "chatid": "CHATID",
  "userid": "zhangsan",
  "content_text": "@机器人 帮我总结一下",
  "mentioned_bot": true,
  "create_time": "2026-05-14T01:00:00+08:00"
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "matched": true,
    "events": [
      {
        "trigger_event_id": 40001,
        "trigger_type": "mention",
        "workflow_code": "reply_generation"
      }
    ]
  },
  "request_id": "req_20260514_000005"
}
```

规则：

- `mentioned_bot=true` 由自建系统识别。
- `@` 触发不调用 Dify 的意图识别。
- 关键词扫描由调度器处理，并作为 `intent_detection` 输入线索。

### 5.2 创建 AI 调用任务

`POST /api/ai/runs`

请求：

```json
{
  "workflow_code": "reply_generation",
  "workflow_version": "v1",
  "trigger_event_id": 40001,
  "response_mode": "blocking",
  "input_json": {}
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "run_id": "airun_20260514_000001",
    "status": "pending"
  },
  "request_id": "req_20260514_000006"
}
```

## 6. Dify 输入输出契约

### 6.1 意图识别与动作决策

workflow_code：`intent_detection`

输入：

```json
{
  "trigger_source": "keyword_scan",
  "chatid": "CHATID",
  "start_time": "2026-05-14T09:00:00+08:00",
  "end_time": "2026-05-14T10:00:00+08:00",
  "matched_keywords": ["报价"],
  "known_users": ["zhangsan", "lisi"],
  "messages": [
    {
      "msgid": "MSGID_1",
      "userid": "zhangsan",
      "content": "李四今天能确认一下报价吗？",
      "create_time": "2026-05-14T09:30:00+08:00"
    }
  ],
  "existing_actions": []
}
```

输出：

```json
{
  "actions": [
    {
      "action_type": "reply",
      "intent_type": "price_confirmation",
      "target_userids": ["lisi"],
      "evidence_msgids": ["MSGID_1"],
      "reason": "消息中明确要求 lisi 确认报价。",
      "reply_instruction": "提醒 lisi 确认报价方案，语气简洁，不要扩展无证据内容。",
      "priority": "medium",
      "confidence": 0.86
    }
  ]
}
```

校验规则：

- `actions` 必须是数组。
- `action_type` 可选：`reply`、`create_task`、`ignore`。
- `target_userids` 必须来自输入 `known_users` 或消息发送人。
- `evidence_msgids` 必须存在于输入消息。
- `reply_instruction` 只是回复指令，不得包含最终可发送正文。
- `confidence < 0.7` 的结果不自动执行。

### 6.2 通用回复生成

workflow_code：`reply_generation`

输入：

```json
{
  "reply_scene": "mention",
  "chatid": "CHATID",
  "source_msgid": "MSGID_1",
  "request_userid": "zhangsan",
  "target_userids": ["lisi"],
  "user_message": "@机器人 帮我总结一下刚才讨论的报价问题",
  "reply_instruction": "基于最近消息总结报价相关讨论，并提醒负责人确认报价方案。",
  "evidence_msgids": ["MSGID_1", "MSGID_2"],
  "recent_messages": [
    {
      "msgid": "MSGID_1",
      "userid": "zhangsan",
      "content": "报价方案今天要确认。",
      "create_time": "2026-05-14T09:30:00+08:00"
    },
    {
      "msgid": "MSGID_2",
      "userid": "lisi",
      "content": "我来确认折扣和交付时间。",
      "create_time": "2026-05-14T09:31:00+08:00"
    }
  ],
  "conversation_summary": null,
  "user_profile": null,
  "runtime": {
    "reply_type": "markdown",
    "language": "zh-CN"
  }
}
```

输出：

```json
{
  "action": "reply",
  "reply": {
    "reply_type": "markdown",
    "content": "刚才主要讨论了报价方案，需要确认折扣和交付时间。建议 lisi 尽快确认报价口径，方便后续同步给客户。"
  },
  "metadata": {
    "reply_scene": "mention",
    "intent_type": "summary_request",
    "evidence_msgids": ["MSGID_1", "MSGID_2"]
  },
  "confidence": 0.9
}
```

校验规则：

- `action` 必须是 `reply` 或 `ignore`。
- `action=reply` 时 `reply.content` 不得为空。
- `metadata.evidence_msgids` 必须来自输入。
- `confidence` 范围为 0 到 1。

### 6.3 会话切分

workflow_code：`conversation_segmentation`

输入：

```json
{
  "chatid": "CHATID",
  "window": {
    "start_time": "2026-05-14T00:00:00+08:00",
    "end_time": "2026-05-14T02:00:00+08:00"
  },
  "messages": [],
  "candidate_boundaries": [],
  "previous_conversation": null
}
```

输出：

```json
{
  "segments": [
    {
      "title": "报价方案讨论",
      "start_msgid": "MSGID_1",
      "end_msgid": "MSGID_20",
      "summary": "本轮主要讨论报价、交付时间和后续负责人。",
      "keywords": ["报价", "交付", "负责人"],
      "participants": ["zhangsan", "lisi"],
      "confidence": 0.88
    }
  ]
}
```

校验规则：

- `start_msgid` 和 `end_msgid` 必须存在于输入消息。
- 会话边界不能交叉。
- 摘要不能为空。

### 6.4 用户画像生成与更新

workflow_code：`user_profile_analysis`

输入：

```json
{
  "userid": "zhangsan",
  "profile_version": 3,
  "current_profile": {
    "summary": "该用户此前主要关注报价和客户跟进。",
    "facts": [
      {
        "fact_id": "FACT_001",
        "fact_type": "interest",
        "label": "关注报价",
        "description": "多次讨论报价和折扣。",
        "confidence": 0.82,
        "status": "active"
      }
    ]
  },
  "recent_messages": [],
  "conversation_summaries": [],
  "statistics": {
    "message_count_30d": 120,
    "active_chats": 3,
    "top_keywords": ["报价", "客户", "交付"]
  }
}
```

输出：

```json
{
  "userid": "zhangsan",
  "profile_action": "update",
  "summary": "该用户持续关注报价和客户跟进，同时近期增加了对交付周期的关注。",
  "facts_to_add": [],
  "facts_to_update": [
    {
      "fact_id": "FACT_001",
      "fact_type": "interest",
      "label": "关注报价和折扣",
      "description": "该用户持续讨论报价，并进一步提到折扣空间。",
      "evidence_msgids": ["MSGID_18"],
      "confidence": 0.86
    }
  ],
  "facts_to_retire": [],
  "confidence": 0.82
}
```

校验规则：

- `userid` 必须与输入一致。
- `profile_action` 可选：`create`、`update`、`no_change`。
- 更新或退役已有事实时必须引用输入中的 `fact_id`。
- 新增或更新事实必须有证据消息或证据会话。

## 7. 发送接口

### 7.1 创建发送任务

`POST /api/outbox-messages`

请求：

```json
{
  "scene": "proactive",
  "chatid": "CHATID",
  "target_userids": ["lisi"],
  "msgtype": "markdown",
  "content": {
    "markdown": {
      "content": "<@lisi> 请确认一下报价方案。"
    }
  },
  "source_type": "ai_run",
  "source_id": "airun_20260514_000002",
  "idempotency_key": "intent_MSGID_1_lisi"
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "outbox_id": "out_20260514_000001",
    "status": "pending"
  },
  "request_id": "req_20260514_000007"
}
```

### 7.2 查询发送记录

`GET /api/outbox-messages?chatid=CHATID&status=failed`

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "items": [],
    "total": 0
  },
  "request_id": "req_20260514_000008"
}
```

## 8. 会话与画像接口

### 8.1 执行会话切分

`POST /api/conversations/segment/run`

请求：

```json
{
  "chatid": "CHATID",
  "start_time": "2026-05-14T00:00:00+08:00",
  "end_time": "2026-05-14T02:00:00+08:00",
  "mode": "auto"
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "run_id": "airun_20260514_000003",
    "status": "pending"
  },
  "request_id": "req_20260514_000009"
}
```

### 8.2 查询会话摘要

`GET /api/conversations?chatid=CHATID`

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "items": [],
    "total": 0
  },
  "request_id": "req_20260514_000010"
}
```

### 8.3 执行用户画像生成与更新

`POST /api/profiles/analyze/run`

请求：

```json
{
  "userid": "zhangsan",
  "mode": "incremental",
  "time_range": {
    "start": "2026-04-14T00:00:00+08:00",
    "end": "2026-05-14T00:00:00+08:00"
  }
}
```

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "run_id": "airun_20260514_000004",
    "status": "pending"
  },
  "request_id": "req_20260514_000011"
}
```

### 8.4 查询用户画像

`GET /api/users/{userid}/profile`

响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "userid": "zhangsan",
    "summary": "该用户近期主要关注报价策略、客户跟进和交付排期。",
    "facts": []
  },
  "request_id": "req_20260514_000012"
}
```

## 9. 管理后台统计接口

### 9.1 总览

`GET /api/dashboard/overview`

返回：

- 今日消息数。
- 累计消息数。
- 活跃群聊数。
- 活跃用户数。
- 今日机器人触发次数。
- Dify 调用次数。
- Dify 成功率。
- 平均响应耗时。
- 待处理任务数。
- 失败任务数。

### 9.2 消息统计

`GET /api/stats/messages?start=2026-05-01&end=2026-05-14&group_by=day`

返回：

- 消息趋势。
- 消息类型分布。
- 群消息排行。
- 用户发言排行。

### 9.3 工作流统计

`GET /api/stats/workflows?start=2026-05-01&end=2026-05-14`

返回：

- 各工作流调用次数。
- 成功率。
- 平均耗时。
- JSON 校验失败次数。
- token 用量。

## 10. 错误码建议

| code | 说明 |
| --- | --- |
| OK | 成功 |
| INVALID_ARGUMENT | 参数错误 |
| UNAUTHORIZED | 未授权 |
| FORBIDDEN | 无权限 |
| NOT_FOUND | 资源不存在 |
| DUPLICATED | 重复请求 |
| EXTERNAL_API_ERROR | 外部 API 错误 |
| DIFY_OUTPUT_INVALID | Dify 输出不合法 |
| WECOM_SEND_FAILED | 企微发送失败 |
| INTERNAL_ERROR | 系统内部错误 |
