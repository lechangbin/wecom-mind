# 03. conversation_segmentation：智能会话切分

## 1. 功能目标

根据群聊消息窗口的时间顺序和语义内容，输出一个或多个主题会话段建议。Dify 只负责提出切分建议，自建系统负责校验边界、生成 `conversation_no`、写库和版本管理。

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
  "chatid": "CHAT_A",
  "window": {
    "start_time": "2026-05-25T09:00:00+08:00",
    "end_time": "2026-05-25T10:00:00+08:00",
    "mode": "manual"
  },
  "messages": [
    {
      "message_id": "201",
      "msgid": "MSG_001",
      "userid": "USER_A",
      "content": "报价方案今天要确认。",
      "create_time": "2026-05-25T09:00:00+08:00"
    },
    {
      "message_id": "202",
      "msgid": "MSG_002",
      "userid": "USER_B",
      "content": "我来确认折扣和交付时间。",
      "create_time": "2026-05-25T09:02:00+08:00"
    }
  ],
  "candidate_boundaries": [],
  "previous_conversation": null
}
```

字段要求：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `chatid` | string | 是 | 群聊 ID |
| `window` | object | 是 | 切分时间窗口 |
| `window.start_time` | string | 是 | 开始时间 |
| `window.end_time` | string | 是 | 结束时间 |
| `window.mode` | string | 否 | `manual`、`scheduled` 等 |
| `messages` | array[object] | 是 | 按时间升序排列 |
| `candidate_boundaries` | array[object] | 是 | 自建系统预切边界，没有则空数组 |
| `previous_conversation` | object/null | 是 | 上一个会话上下文 |

## 3. 节点编排

```text
Start(payload)
  -> Code: normalize_segmentation_input
  -> LLM: segment_conversation
  -> Code: finalize_segments
  -> End(segments)
```

### normalize_segmentation_input

输出给 LLM 的字段：

- `chatid: string`
- `window: object`
- `messages: array[object]`
- `candidate_boundaries: array[object]`
- `previous_conversation: object`
- `valid_msgids: array[string]`
- `valid_userids: array[string]`
- `fallback: object`

归一化规则：

- `messages` 不是数组时视为空数组。
- 过滤没有 `msgid` 的消息。
- `valid_msgids` 按输入顺序生成。
- `valid_userids` 来自 `messages[].userid`，去重。

### segment_conversation

LLM 任务：

- 判断消息窗口中是否存在一个或多个清晰主题段。
- 为每个会话段生成标题、摘要、关键词、参与人和置信度。
- 只使用输入消息作为起止边界。
- 如果主题不清晰或消息不足，返回空数组。

LLM 输出必须是：

```json
{
  "segments": [
    {
      "title": "报价方案确认",
      "start_msgid": "MSG_001",
      "end_msgid": "MSG_002",
      "summary": "本段主要讨论报价方案、折扣和交付时间的确认。",
      "keywords": ["报价", "折扣", "交付时间"],
      "participants": ["USER_A", "USER_B"],
      "confidence": 0.88
    }
  ]
}
```

Prompt 必须强调：

- `start_msgid` 和 `end_msgid` 必须来自 `valid_msgids`。
- `participants` 必须来自 `valid_userids`。
- 会话区间不能重叠或交叉。
- `summary` 不能空泛，必须能独立表达本段主题。
- 不要输出自建系统才生成的 `conversation_no`。

### finalize_segments

校验规则：

- `segments` 缺失或不是数组时返回空数组。
- 删除起止 `msgid` 不存在的 segment。
- 删除 `start_msgid` 在输入顺序中晚于 `end_msgid` 的 segment。
- 删除参与人不在输入消息中的 segment。
- 删除和前一段重叠的 segment。
- `confidence` 必须在 0 到 1。

## 4. End 输出

End 节点只声明：

| 字段 | 类型 |
| --- | --- |
| `segments` | `array[object]` |

兜底输出：

```json
{
  "segments": []
}
```

## 5. 验收样例

成功样例：

```json
{
  "segments": [
    {
      "title": "报价方案确认",
      "start_msgid": "MSG_001",
      "end_msgid": "MSG_002",
      "summary": "本段主要讨论报价方案、折扣和交付时间。",
      "keywords": ["报价", "折扣", "交付时间"],
      "participants": ["USER_A", "USER_B"],
      "confidence": 0.88
    }
  ]
}
```

空输入或无法切分时：

```json
{
  "segments": []
}
```

非法输出风险：

- 输出不存在的起止消息。
- 输出交叉区间。
- 输出不存在的参与人。
- 输出 `conversation_no`，覆盖自建系统职责。

