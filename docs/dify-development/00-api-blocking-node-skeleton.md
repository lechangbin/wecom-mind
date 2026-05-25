# 00. API Blocking 与通用节点骨架

本文档定义所有 Dify 工作流的共同实现方式。后续 agent 配置任何 workflow 前，必须先按本文档建立同一套骨架。

## 1. API 调用契约

自建系统通过 Dify API blocking 调用工作流：

```http
POST /v1/workflows/run
Authorization: Bearer <DIFY_API_KEY>
Content-Type: application/json
```

请求体固定为：

```json
{
  "inputs": {
    "payload": {
      "workflow_specific_field": "value"
    }
  },
  "response_mode": "blocking",
  "user": "system-or-operator"
}
```

约定：

- `payload` 内部才是业务 JSON。
- 不要把 `messages`、`known_users`、`recent_messages`、`evidence_msgids` 等数组拆成 Start 表单变量。
- 不使用 Webhook body 作为本阶段主链路。
- 后续如需 Webhook，只能作为单独架构扩展，不得影响当前 API blocking 文档。

## 2. Start 节点

所有 workflow 的 Start 节点只声明一个输入变量：

| 字段 | 值 |
| --- | --- |
| variable | `payload` |
| type | `json_object` |
| required | `true` |

Dify 1.14.2 不接受 Start 变量类型 `json`。复杂业务字段统一放入 `payload` 对象中，由 Code 节点提取。

## 3. 推荐节点链路

```text
Start(payload)
  -> Code: normalize_input
  -> LLM: business_reasoning
  -> Code: finalize_json
  -> End
```

### Code: normalize_input

职责：

- 从 `payload` 提取字段。
- 校验必填字段的基本类型。
- 将缺失的数组字段归一化为空数组。
- 将缺失的对象字段归一化为 `null` 或空对象。
- 只输出 LLM 需要的结构化变量。

要求：

- Code language 使用 `python3` 或 `javascript`。
- Code outputs 只使用 Dify 支持类型：`string`、`number`、`object`、`boolean`、`array[string]`、`array[number]`、`array[object]`、`array[boolean]`。
- Code 返回 key 必须和 outputs 声明完全一致。

### LLM: business_reasoning

职责：

- 完成语义判断、摘要、回复生成或画像推理。
- 只输出 JSON 字符串或 JSON 对象，不输出解释性自然语言。
- 必须遵守对应 workflow 的输出字段、证据合法性和兜底规则。

提示词必须包含：

- Dify 角色和工作流目标。
- 输入字段说明。
- 输出 JSON schema 摘要。
- 严禁编造 `msgid`、`userid`。
- 低置信度兜底输出。
- `intent_detection` 和 `reply_generation` 的边界。

### Code: finalize_json

职责：

- 解析 LLM 输出。
- 丢弃 JSON 外自然语言。
- 校验必填字段和字段类型。
- 修正可安全修正的小问题，例如缺失数组改为空数组。
- 对不可安全修正的输出返回兜底 JSON。
- 确保 End 节点只接收正式业务字段。

关键规则：

- 不要补造不存在的证据消息或用户。
- 发现证据非法时，删除该子项或返回兜底。
- `confidence` 超出 0 到 1 时必须夹取或兜底。
- LLM 输出无法解析时必须返回对应 workflow 的兜底结果。

## 4. End 节点

End 节点 outputs 必须直接声明正式业务字段。

| workflow_code | End outputs |
| --- | --- |
| `intent_detection` | `actions: array[object]` |
| `reply_generation` | `action: string`、`reply: object`、`metadata: object`、`confidence: number` |
| `conversation_segmentation` | `segments: array[object]` |
| `user_profile_analysis` | `userid: string`、`profile_action: string`、`summary: string`、`facts_to_add: array[object]`、`facts_to_update: array[object]`、`facts_to_retire: array[object]`、`confidence: number` |

不允许 End 只输出 `result`、`output`、`text` 或临时字段。

## 5. 验收方式

后续 agent 完成 Dify 配置后，必须用 blocking API 逐个运行测试：

1. 成功样例：返回业务字段且 `data.status=succeeded`。
2. 空输入或弱相关样例：返回兜底 JSON。
3. 非法输出防护样例：LLM 输出无法解析或引用非法证据时，finalize Code 返回兜底或删除非法子项。

验收时看 Dify API 响应中的：

```json
{
  "data": {
    "status": "succeeded",
    "outputs": {}
  }
}
```

`outputs` 必须已经是正式字段结构。

