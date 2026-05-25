# 01. intent_detection：意图识别与动作决策

## 1. 功能目标

分析一个已入库的群聊消息窗口，结合扫描来源、关键词线索和已知用户，判断是否需要后续动作。

该工作流只输出动作建议，不输出最终可发送的回复正文。需要回复时，只能输出 `reply_instruction`，由自建系统再调用 `reply_generation`。

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
  "trigger_source": "schedule_scan",
  "chatid": "CHAT_A",
  "start_time": "2026-05-25T09:00:00+08:00",
  "end_time": "2026-05-25T10:00:00+08:00",
  "matched_keywords": ["报价"],
  "known_users": ["USER_A", "USER_B"],
  "messages": [
    {
      "message_id": "101",
      "msgid": "MSG_001",
      "chatid": "CHAT_A",
      "userid": "USER_A",
      "content": "USER_B 今天能确认报价吗？客户下午等反馈。",
      "create_time": "2026-05-25T09:30:00+08:00"
    }
  ],
  "existing_actions": []
}
```

字段要求：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `trigger_source` | string | 是 | `keyword_scan`、`schedule_scan`、`manual` |
| `chatid` | string | 是 | 群聊 ID |
| `start_time` | string | 是 | 扫描窗口开始时间 |
| `end_time` | string | 是 | 扫描窗口结束时间 |
| `matched_keywords` | array[string] | 是 | 命中关键词，没有则空数组 |
| `known_users` | array[string] | 是 | 当前窗口内已知 userid |
| `messages` | array[object] | 是 | 按时间升序排列的消息 |
| `existing_actions` | array[object] | 是 | 已处理动作，没有则空数组 |

## 3. 节点编排

```text
Start(payload)
  -> Code: normalize_intent_input
  -> If/Else: has_messages
    true  -> Template Transform: build_intent_context
          -> LLM: detect_actions
          -> Code: finalize_actions
          -> Variable Aggregator: actions_output
          -> End(actions)
    false -> Variable Aggregator: actions_output
          -> End(actions)
```

节点原则：

- `normalize_intent_input` 只做 payload 字段读取、类型归一化、合法 `msgid/userid` 集合和兜底输出。
- `has_messages` 使用 `messages` 是否为空或 `has_required_context` 判断是否调用 LLM。
- `build_intent_context` 用 Template Transform 格式化扫描窗口、关键词、已知用户、消息时间线和输出规则。
- `actions_output` 优先聚合 `finalize_actions.actions`，未进入 LLM 时聚合 `normalize_intent_input.fallback_actions`。

```text
actions_output.variables:
  1. finalize_actions.actions
  2. normalize_intent_input.fallback_actions
```

### normalize_intent_input

输出给 LLM 的字段：

- `trigger_source: string`
- `chatid: string`
- `time_window: object`
- `matched_keywords: array[string]`
- `known_users: array[string]`
- `messages: array[object]`
- `existing_actions: array[object]`
- `valid_msgids: array[string]`
- `valid_userids: array[string]`
- `has_required_context: boolean`
- `fallback_actions: array[object]`
- `fallback: object`

归一化规则：

- `messages` 不是数组时视为空数组。
- `known_users` 不是数组时视为空数组。
- 每条消息只保留 `msgid`、`userid`、`content`、`create_time`、`chatid`。
- 缺少 `msgid` 的消息不作为证据。
- 兜底对象固定为 `{"actions":[]}`。
- `fallback_actions` 固定为空数组。
- `has_required_context` 在存在至少一条带 `msgid` 的消息时为 `true`。

### build_intent_context

Template Transform 输入：

- `trigger_source`
- `chatid`
- `time_window`
- `matched_keywords`
- `known_users`
- `messages`
- `existing_actions`
- `valid_msgids`
- `valid_userids`

输出：

- `output: string`

模板内容必须清晰展示消息时间线、合法证据 ID、合法用户 ID、可输出 action 类型和禁止输出最终回复正文的规则。

### detect_actions

LLM 任务：

- 判断窗口内是否存在明确待处理事项。
- 关键词只作为线索，不等于必须输出动作。
- 可输出 `reply`、`create_task`、`ignore`。
- `action_type=reply` 时输出 `reply_instruction`，不能输出回复正文。

LLM 输出必须是：

```json
{
  "actions": [
    {
      "action_type": "reply",
      "intent_type": "price_confirmation",
      "target_userids": ["USER_B"],
      "evidence_msgids": ["MSG_001"],
      "reason": "群聊中要求 USER_B 确认报价，并提到客户等待反馈。",
      "reply_instruction": "提醒 USER_B 确认报价方案，语气简洁，不扩展无证据内容。",
      "priority": "medium",
      "confidence": 0.86
    }
  ]
}
```

Prompt 必须强调：

- `target_userids` 必须来自 `known_users` 或输入消息发送人。
- `evidence_msgids` 必须来自 `messages[].msgid`。
- 低于 0.7 置信度的事项不要放入 `actions`。
- 不能输出最终回复正文。

### finalize_actions

校验规则：

- `actions` 缺失或不是数组时返回空数组。
- 删除缺少证据的 action。
- 删除引用不存在 `msgid` 的 action。
- 删除引用不存在 `userid` 的 action。
- `action_type=reply` 且缺少 `reply_instruction` 时删除该 action。
- `confidence` 低于 0.7 的 action 不进入输出。

## 4. End 输出

End 节点只声明：

| 字段 | 类型 |
| --- | --- |
| `actions` | `array[object]` |

兜底输出：

```json
{
  "actions": []
}
```

## 5. 验收样例

成功样例必须输出至少一条 action，且证据来自输入：

```json
{
  "actions": [
    {
      "action_type": "reply",
      "intent_type": "price_confirmation",
      "target_userids": ["USER_B"],
      "evidence_msgids": ["MSG_001"],
      "reason": "需要确认报价。",
      "reply_instruction": "提醒 USER_B 确认报价。",
      "priority": "medium",
      "confidence": 0.86
    }
  ]
}
```

弱相关样例必须输出：

```json
{
  "actions": []
}
```

非法输出风险：

- LLM 输出 `content`、`reply`、`suggested_message` 作为最终正文。
- LLM 使用输入中不存在的 `MSG_X` 或 `USER_X`。
- LLM 只因为命中关键词就强行输出动作。
