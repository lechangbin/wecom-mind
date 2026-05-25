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

Dify 1.14.2 不接受 Start 变量类型 `json`。复杂业务字段统一放入 `payload` 对象中，由最小化的 normalize Code 节点提取；输入门禁、上下文拼装和分支汇合优先使用 Dify 官方预置节点。

## 3. 推荐节点链路

默认采用“官方节点优先”的可视化链路：

```text
Start(payload)
  -> Code: normalize_input
  -> If/Else: input_gate
    true  -> Template Transform: build_llm_context
          -> LLM: business_reasoning
          -> Code: finalize_json
          -> Variable Aggregator: merge_outputs
          -> End
    false -> Variable Aggregator: merge_outputs
          -> End
```

可裁剪规则：

- 如果输入永远由自建系统保证完整，可以省略 `If/Else`，但仍建议保留兜底验收。
- 如果 workflow 只有单一路径，可以省略 `Variable Aggregator`，直接从 `finalize_json` 到 End。
- 不要为了可视化删除 `finalize_json`，最终 JSON 解析、证据校验和兜底必须确定性执行。

### Code: normalize_input

职责：

- 从 `payload` 提取字段。
- 校验必填字段的基本类型。
- 将缺失的数组字段归一化为空数组。
- 将缺失的对象字段归一化为 `null` 或空对象。
- 输出 LLM 需要的结构化变量。
- 输出 `has_required_context: boolean`，供 `If/Else` 分流。
- 输出 workflow 对应的兜底字段，例如 `fallback_actions`、`fallback_segments` 或 `fallback_result`。

要求：

- Code language 使用 `python3` 或 `javascript`。
- Code outputs 只使用 Dify 支持类型：`string`、`number`、`object`、`boolean`、`array[string]`、`array[number]`、`array[object]`、`array[boolean]`。
- Code 返回 key 必须和 outputs 声明完全一致。

### If/Else: input_gate

职责：

- 根据 `normalize_input.has_required_context` 判断是否进入 LLM。
- 空输入、消息不足、缺少 `userid` 等场景直接走兜底分支。

要求：

- If 分支进入 `Template Transform` 和 LLM。
- Else 分支直接进入 `Variable Aggregator`。
- 不在 If/Else 里做复杂业务判断；复杂判断仍由 LLM 或 finalize Code 完成。

### Template Transform: build_llm_context

职责：

- 使用 Jinja2 模板把消息列表、画像摘要、会话摘要、合法 ID 集合整理成 LLM 可读上下文。
- 把输出 schema、边界规则和兜底规则拆成清晰文本片段。
- 输出单个 `output: string`，供 LLM 引用。

要求：

- Template Transform 只负责格式化，不负责事实判断。
- 不要在模板里生成最终业务 JSON。
- 数组和对象仍以 normalize Code 输出的结构化变量为准。

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

### Variable Aggregator: merge_outputs

职责：

- 汇合成功分支和兜底分支。
- 优先选择 `finalize_json` 输出；如果 LLM 分支未执行，则选择 `normalize_input` 的兜底字段。

要求：

- 聚合变量必须是同类型，例如 `array[object]` 对 `array[object]`、`string` 对 `string`。
- 多字段 workflow 可使用 grouped aggregation，或为每个 End 字段单独配置一个 Variable Aggregator。
- Variable Aggregator 不是字段提取节点，不要用它解析 `payload`。

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
