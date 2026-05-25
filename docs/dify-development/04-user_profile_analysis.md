# 04. user_profile_analysis：用户画像生成与更新

## 1. 功能目标

根据目标用户近期消息、参与会话摘要、当前画像和统计特征，输出画像新增、更新、退役或不变建议。

Dify 只输出画像演进建议，自建系统负责校验、合并、版本号、写库和事实状态管理。

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
  "userid": "USER_A",
  "profile_version": 2,
  "recent_messages": [
    {
      "message_id": "301",
      "msgid": "MSG_001",
      "chatid": "CHAT_A",
      "userid": "USER_A",
      "content": "客户比较关心报价和交付周期。",
      "create_time": "2026-05-25T09:00:00+08:00"
    }
  ],
  "conversation_summaries": [
    {
      "conversation_no": "conv_20260525_abcd12",
      "title": "报价讨论",
      "summary": "本段讨论报价、折扣和交付周期。",
      "keywords": ["报价", "交付"],
      "participants": ["USER_A", "USER_B"],
      "start_time": "2026-05-25T09:00:00+08:00",
      "end_time": "2026-05-25T09:20:00+08:00"
    }
  ],
  "current_profile": {
    "userid": "USER_A",
    "summary": "该用户此前主要关注报价。",
    "facts": [
      {
        "fact_id": "FACT_001",
        "fact_type": "interest",
        "label": "关注报价",
        "description": "多次讨论报价。",
        "evidence_msgids": ["MSG_OLD_001"],
        "confidence": 0.82,
        "status": "active"
      }
    ],
    "confidence": 0.8,
    "version": 1,
    "status": "active"
  },
  "statistics": {
    "message_count_30d": 1,
    "active_chats": 1,
    "top_keywords": ["报价", "交付"]
  },
  "runtime": {
    "mode": "manual",
    "language": "zh-CN"
  }
}
```

字段要求：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `userid` | string | 是 | 目标用户 ID |
| `profile_version` | number | 是 | 自建系统分配的下一版本号 |
| `recent_messages` | array[object] | 是 | 目标用户近期消息 |
| `conversation_summaries` | array[object] | 是 | 目标用户参与过的会话摘要 |
| `current_profile` | object/null | 是 | 当前有效画像，没有则 null |
| `statistics` | object | 是 | 统计特征 |
| `runtime` | object | 否 | 分析模式和语言 |

## 3. 节点编排

```text
Start(payload)
  -> Code: normalize_profile_input
  -> If/Else: has_userid_and_evidence
    true  -> Template Transform: build_profile_context
          -> LLM: analyze_profile_delta
          -> Code: finalize_profile
          -> Variable Aggregator: profile_outputs
          -> End(userid, profile_action, summary, facts_to_add, facts_to_update, facts_to_retire, confidence)
    false -> Variable Aggregator: profile_outputs
          -> End(userid, profile_action, summary, facts_to_add, facts_to_update, facts_to_retire, confidence)
```

节点原则：

- `normalize_profile_input` 只做 payload 字段读取、证据集合、当前事实集合和兜底字段。
- `has_userid_and_evidence` 在 `userid` 缺失、近期消息和会话摘要都为空时直接走兜底。
- `build_profile_context` 用 Template Transform 拼装近期消息、会话摘要、当前画像、统计特征和敏感信息约束。
- `profile_outputs` 使用 grouped aggregation 或多个 Variable Aggregator，优先取 `finalize_profile` 输出，未进入 LLM 时取 `normalize_profile_input` 的兜底字段。

### normalize_profile_input

输出给 LLM 的字段：

- `userid: string`
- `profile_version: number`
- `recent_messages: array[object]`
- `conversation_summaries: array[object]`
- `current_profile: object`
- `statistics: object`
- `valid_msgids: array[string]`
- `valid_conversation_nos: array[string]`
- `current_fact_ids: array[string]`
- `has_required_context: boolean`
- `fallback_userid: string`
- `fallback_profile_action: string`
- `fallback_summary: string`
- `fallback_facts_to_add: array[object]`
- `fallback_facts_to_update: array[object]`
- `fallback_facts_to_retire: array[object]`
- `fallback_confidence: number`
- `fallback: object`

归一化规则：

- `recent_messages` 不是数组时视为空数组。
- `conversation_summaries` 不是数组时视为空数组。
- `current_profile` 缺失时视为 `null`。
- `current_fact_ids` 来自 `current_profile.facts[].fact_id`。
- 兜底输出中的 `userid` 必须使用输入 `userid`。
- `has_required_context` 在存在 `userid` 且近期消息或会话摘要至少一项不为空时为 `true`。
- 各 `fallback_*` 字段必须与兜底输出逐字段一致。

### build_profile_context

Template Transform 输入：

- `userid`
- `profile_version`
- `recent_messages`
- `conversation_summaries`
- `current_profile`
- `statistics`
- `valid_msgids`
- `valid_conversation_nos`
- `current_fact_ids`

输出：

- `output: string`

模板内容必须把当前画像、可更新事实 ID、合法证据消息、合法会话编号和敏感信息保守规则分区展示。

### analyze_profile_delta

LLM 任务：

- 判断当前证据是否足以创建、更新或保持画像。
- 从近期消息和会话摘要中提取稳定偏好、关注点、角色或风险。
- 只输出有证据支撑的事实。
- 对敏感个人信息保守处理，优先不输出或降低置信度。

LLM 输出必须是：

```json
{
  "userid": "USER_A",
  "profile_action": "update",
  "summary": "该用户持续关注报价，并开始关注交付周期。",
  "facts_to_add": [
    {
      "fact_type": "interest",
      "label": "关注交付周期",
      "description": "近期消息中提到客户关心交付周期。",
      "evidence_msgids": ["MSG_001"],
      "evidence_conversation_nos": ["conv_20260525_abcd12"],
      "confidence": 0.78
    }
  ],
  "facts_to_update": [
    {
      "fact_id": "FACT_001",
      "fact_type": "interest",
      "label": "关注报价和交付",
      "description": "该用户持续关注报价，并新增对交付周期的关注。",
      "evidence_msgids": ["MSG_001"],
      "confidence": 0.84
    }
  ],
  "facts_to_retire": [],
  "confidence": 0.82
}
```

Prompt 必须强调：

- 输出 `userid` 必须等于输入 `userid`。
- `facts_to_update[].fact_id` 和 `facts_to_retire[].fact_id` 必须来自 `current_fact_ids`。
- `evidence_msgids` 必须来自 `valid_msgids`。
- `evidence_conversation_nos` 必须来自 `valid_conversation_nos`。
- 证据不足时返回 `no_change`。

### finalize_profile

校验规则：

- `userid` 不一致时返回兜底。
- `profile_action` 只能是 `create`、`update`、`no_change`。
- 删除无证据的新增或更新事实。
- 删除引用不存在 `msgid` 的事实。
- 删除引用不存在 `conversation_no` 的事实。
- 删除引用不存在 `fact_id` 的更新或退役项。
- `confidence` 必须在 0 到 1。

## 4. End 输出

End 节点声明：

| 字段 | 类型 |
| --- | --- |
| `userid` | `string` |
| `profile_action` | `string` |
| `summary` | `string` |
| `facts_to_add` | `array[object]` |
| `facts_to_update` | `array[object]` |
| `facts_to_retire` | `array[object]` |
| `confidence` | `number` |

兜底输出：

```json
{
  "userid": "USER_A",
  "profile_action": "no_change",
  "summary": "",
  "facts_to_add": [],
  "facts_to_update": [],
  "facts_to_retire": [],
  "confidence": 0.0
}
```

其中 `userid` 必须来自输入。

## 5. 验收样例

成功样例：

```json
{
  "userid": "USER_A",
  "profile_action": "update",
  "summary": "该用户持续关注报价和交付周期。",
  "facts_to_add": [],
  "facts_to_update": [
    {
      "fact_id": "FACT_001",
      "fact_type": "interest",
      "label": "关注报价和交付",
      "description": "近期消息中同时提到报价和交付周期。",
      "evidence_msgids": ["MSG_001"],
      "confidence": 0.84
    }
  ],
  "facts_to_retire": [],
  "confidence": 0.82
}
```

证据不足时：

```json
{
  "userid": "USER_A",
  "profile_action": "no_change",
  "summary": "",
  "facts_to_add": [],
  "facts_to_update": [],
  "facts_to_retire": [],
  "confidence": 0.0
}
```

非法输出风险：

- 输出其他 `userid`。
- 更新或退役不存在的 `fact_id`。
- 用历史证据消息替代本次 `recent_messages` 证据。
- 把敏感、模糊或单次偶然表达写成确定画像事实。
