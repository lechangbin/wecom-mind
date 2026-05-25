# 06. 官方节点优先的可视化编排

本文档补充所有 Dify workflow 的节点选择原则。目标是在不牺牲 JSON 契约和兜底可靠性的前提下，尽量使用 Dify 官方预置节点，让工作流在画布上更容易理解、审查和交给后续 agent 并行开发。

## 1. 总原则

优先使用官方节点表达流程语义：

- `If/Else` 表达输入是否足够、是否需要进入 LLM、是否走兜底。
- `Template Transform` 表达 Prompt 上下文拼装、消息时间线格式化、schema 提示片段。
- `Variable Aggregator` 表达分支结果汇合，优先选择成功分支输出，失败或空输入时选择兜底输出。
- `Parameter Extractor` 只用于从自然语言文本中抽取参数，不用于解析 `payload` JSON。
- `Question Classifier` 可用于明显的文本意图分流，但不是四个 workflow 的必需节点。

Code 节点保留在两个确定性边界：

- `normalize_*`：从 `payload` 读取字段、做类型归一化、生成合法证据集合和兜底字段。
- `finalize_*`：解析 LLM 输出、校验 schema、校验证据引用、返回正式字段或兜底。

不建议把严格校验交给 LLM、`Template Transform` 或 `Parameter Extractor`。这些节点提升可视化，不替代确定性 JSON 校验。

## 2. 推荐通用链路

```text
Start(payload)
  -> Code: normalize_payload
  -> If/Else: input_gate
    true  -> Template Transform: build_llm_context
          -> LLM: business_reasoning
          -> Code: finalize_json
          -> Variable Aggregator: merge_outputs
          -> End(正式字段)
    false -> Variable Aggregator: merge_outputs
          -> End(正式字段)
```

说明：

- `normalize_payload` 必须输出 `has_required_context: boolean` 和各 workflow 的兜底字段。
- `input_gate` 用 `has_required_context` 或消息数组是否为空来控制是否调用 LLM。
- `build_llm_context` 输出单个字符串，作为 LLM 的主要上下文变量。
- `finalize_json` 输出正式字段。
- `merge_outputs` 先选择 `finalize_json` 的正式字段；如果该分支未执行，再选择 `normalize_payload` 的兜底字段。

## 3. 节点替换矩阵

| 原 Code 职责 | 优先替换节点 | 是否替换 | 说明 |
| --- | --- | --- | --- |
| 从 `payload` 读取字段 | Code | 不替换 | Dify 官方节点不适合深层 JSON 读取和类型归一化。 |
| 判断消息是否为空 | `If/Else` | 替换 | normalize Code 输出布尔值，If/Else 负责可视化分流。 |
| 拼接消息时间线 | `Template Transform` | 替换 | 用 Jinja2 把数组、摘要、画像转成 LLM 上下文。 |
| 拼接输出 schema 提示 | `Template Transform` | 替换 | schema 文案可单独模板化，减少 LLM 节点内大段提示。 |
| 从用户自然语言抽取回复类型/需求 | `Parameter Extractor` | 可选替换 | 仅适用于 `user_message` 或人工指令，不用于解析 JSON。 |
| 意图大类粗分流 | `Question Classifier` | 可选替换 | 适合后续扩展，不作为当前必需节点。 |
| LLM JSON 解析 | Code | 不替换 | 必须确定性处理代码块、散文、非法 JSON。 |
| `msgid`、`userid` 合法性校验 | Code | 不替换 | 必须和输入集合精确比对。 |
| 成功分支与兜底分支合并 | `Variable Aggregator` | 替换 | 使用 first available 语义汇合分支输出。 |
| 多条消息逐条处理 | `Iteration` | 暂不替换 | 会增加延迟和复杂度；只有后续需要逐条 LLM 判断时再引入。 |
| 调业务接口、写库、发送企微 | 不使用 Dify 节点 | 不纳入 | 这些仍属于自建系统职责。 |

## 4. Variable Aggregator 使用规则

`Variable Aggregator` 不是字段提取节点。它的作用是把不同分支的同类型变量汇合成一个输出。

推荐用法：

```text
variables:
  1. finalize_json.actions
  2. normalize_payload.fallback_actions
```

含义：

- true 分支执行完成时，优先输出 `finalize_json.actions`。
- false 分支直接进入汇合点时，`finalize_json.actions` 不存在，因此输出 `normalize_payload.fallback_actions`。

多字段 workflow 有两种做法：

- 优先：使用 grouped aggregation，每组对应一个 End 字段，例如 `action`、`reply`、`metadata`、`confidence`。
- 备选：每个 End 字段单独放一个 Variable Aggregator，配置更啰嗦但更直观。
- 如果某个正式字段允许 `null`，而 Dify 聚合器不接受该类型，则该字段不要强行聚合，改用 finalize Code 或单独兜底分支保证 End 输出类型符合文档。

不要把 `payload.messages` 直接接到 Variable Aggregator 里期待它“提取 messages”。字段提取仍由 `normalize_payload` 完成。

## 5. Parameter Extractor 使用规则

`Parameter Extractor` 适合从自然语言里抽取结构化参数，例如：

- `reply_generation.user_message` 中的请求类型：总结、改写、回答、确认。
- 人工触发时 `reply_instruction` 中的语气、长度、输出语言。
- 后续扩展任务里从一句话中抽取任务标题、截止时间、负责人候选。

限制：

- 不用于解析 `payload`。
- 不作为最终事实来源，只作为 LLM 的辅助输入。
- 抽取失败时不能中断主流程，必须允许走默认值或兜底。

## 6. 四个 workflow 的推荐拓扑

| workflow_code | 推荐拓扑 |
| --- | --- |
| `intent_detection` | `Start -> normalize_intent_input -> If/Else(has_messages) -> Template Transform(intent_context) -> LLM -> finalize_actions -> Variable Aggregator(actions) -> End` |
| `reply_generation` | `Start -> normalize_reply_input -> If/Else(can_reply) -> Template Transform(reply_context) -> optional Parameter Extractor(reply_request) -> LLM -> finalize_reply -> grouped Variable Aggregator -> End` |
| `conversation_segmentation` | `Start -> normalize_segmentation_input -> If/Else(has_enough_messages) -> Template Transform(timeline_context) -> LLM -> finalize_segments -> Variable Aggregator(segments) -> End` |
| `user_profile_analysis` | `Start -> normalize_profile_input -> If/Else(has_userid_and_evidence) -> Template Transform(profile_context) -> LLM -> finalize_profile -> grouped Variable Aggregator -> End` |

## 7. 验收补充

后续 agent 配置 Dify 后，除了原有 blocking API 验收，还要人工检查画布：

- 是否能从节点标题直接看出输入门禁、上下文构造、业务推理、校验兜底、结果汇合。
- 是否只有 normalize/finalize 两类 Code 节点。
- 是否没有把临时字段接到 End。
- 是否没有把 Webhook ack 或外部副作用节点放进主链路。
