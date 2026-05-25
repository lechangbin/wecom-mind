# 02. reply_generation：通用回复生成

## 1. 功能目标

根据显式 @、人工触发或 `intent_detection` 给出的回复指令，生成最终可发送到企业微信的回复正文。

该工作流是唯一允许输出可发送消息正文的 Dify 工作流。

## 2. Start 输入

Start 节点只声明：

```json
{
  "variable": "payload",
  "type": "json_object",
  "required": true
}
```

自建系统传入的 `payload`：

```json
{
  "reply_scene": "mention",
  "chatid": "CHAT_A",
  "source_msgid": "MSG_001",
  "request_userid": "USER_A",
  "target_userids": [],
  "user_message": "@机器人 帮我总结刚才报价问题",
  "reply_instruction": "直接回应用户 @ 机器人的消息，基于输入上下文生成简洁回复。",
  "evidence_msgids": ["MSG_001"],
  "recent_messages": [
    {
      "msgid": "MSG_001",
      "userid": "USER_A",
      "content": "@机器人 帮我总结刚才报价问题",
      "create_time": "2026-05-25T09:30:00+08:00"
    }
  ],
  "conversation_summary": null,
  "user_profile": null,
  "runtime": {
    "response_mode": "blocking",
    "reply_type": "markdown",
    "language": "zh-CN"
  }
}
```

字段要求：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `reply_scene` | string | 是 | `mention`、`proactive`、`manual` |
| `chatid` | string | 是 | 群聊 ID |
| `source_msgid` | string | 是 | 触发回复的源消息 |
| `request_userid` | string/null | 是 | 请求用户 |
| `target_userids` | array[string] | 是 | 回复面向用户，没有则空数组 |
| `user_message` | string/null | 是 | 用户原始请求或触发摘要 |
| `reply_instruction` | string | 是 | 回复生成要求 |
| `evidence_msgids` | array[string] | 是 | 证据消息 ID |
| `recent_messages` | array[object] | 是 | 最近上下文 |
| `conversation_summary` | object/null | 是 | 会话摘要 |
| `user_profile` | object/null | 是 | 用户画像摘要 |
| `runtime` | object | 是 | 回复类型、语言、响应模式 |

## 3. 节点编排

```text
Start(payload)
  -> Code: normalize_reply_input
  -> LLM: generate_reply
  -> Code: finalize_reply
  -> End(action, reply, metadata, confidence)
```

### normalize_reply_input

输出给 LLM 的字段：

- `reply_scene: string`
- `chatid: string`
- `source_msgid: string`
- `request_userid: string`
- `target_userids: array[string]`
- `user_message: string`
- `reply_instruction: string`
- `evidence_msgids: array[string]`
- `recent_messages: array[object]`
- `conversation_summary: object`
- `user_profile: object`
- `runtime: object`
- `fallback: object`

归一化规则：

- `runtime.reply_type` 缺失时默认 `markdown`。
- `runtime.language` 缺失时默认 `zh-CN`。
- `recent_messages` 不是数组时视为空数组。
- `evidence_msgids` 只保留出现在 `recent_messages[].msgid` 中的值。

### generate_reply

LLM 任务：

- 根据 `reply_instruction` 和上下文生成简洁、可直接发送的回复。
- 只基于输入消息、会话摘要和用户画像，不编造事实。
- 不决定是否实际发送，不写 @ 提及逻辑，不处理幂等。

LLM 输出必须是：

```json
{
  "action": "reply",
  "reply": {
    "reply_type": "markdown",
    "content": "刚才主要讨论了报价方案，需要确认折扣和交付时间。建议先统一报价口径，再同步给客户。"
  },
  "metadata": {
    "reply_scene": "mention",
    "intent_type": "summary_request",
    "evidence_msgids": ["MSG_001"]
  },
  "confidence": 0.9
}
```

Prompt 必须强调：

- 这是唯一可输出最终回复正文的 workflow。
- `reply.content` 必须适合直接发到群里。
- `metadata.evidence_msgids` 必须来自输入。
- 不能把系统说明、JSON schema、推理过程写入 `reply.content`。
- 无法安全回复时输出兜底 `ignore`。

### finalize_reply

校验规则：

- `action` 只能是 `reply` 或 `ignore`。
- `action=reply` 时 `reply.content` 必须非空。
- `reply.reply_type` 只能是 `markdown` 或 `text`。
- `metadata.evidence_msgids` 必须来自输入 `evidence_msgids` 或 `recent_messages[].msgid`。
- `confidence` 必须在 0 到 1。
- 输出无法解析或正文为空时返回兜底。

## 4. End 输出

End 节点声明：

| 字段 | 类型 |
| --- | --- |
| `action` | `string` |
| `reply` | `object` |
| `metadata` | `object` |
| `confidence` | `number` |

兜底输出：

```json
{
  "action": "ignore",
  "reply": null,
  "metadata": {
    "reply_scene": "unknown",
    "intent_type": "unknown",
    "evidence_msgids": []
  },
  "confidence": 0.0
}
```

## 5. 验收样例

成功样例：

```json
{
  "action": "reply",
  "reply": {
    "reply_type": "markdown",
    "content": "收到，我会基于当前上下文整理报价相关信息。"
  },
  "metadata": {
    "reply_scene": "mention",
    "intent_type": "summary_request",
    "evidence_msgids": ["MSG_001"]
  },
  "confidence": 0.9
}
```

无法安全回复时：

```json
{
  "action": "ignore",
  "reply": null,
  "metadata": {
    "reply_scene": "unknown",
    "intent_type": "unknown",
    "evidence_msgids": []
  },
  "confidence": 0.0
}
```

非法输出风险：

- `reply.content` 包含 JSON 说明、推理过程或不可发送文本。
- `metadata.evidence_msgids` 引用不存在的消息。
- `intent_detection` 以外的工作流绕过本工作流输出正文。

