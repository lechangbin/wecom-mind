# 05. Agent 开发指引

本文档用于把 Dify 功能开发拆给后续 agent。当前阶段只做 Dify 功能文档和配置指引；真正配置 Dify、改自建系统代码、接真实模型时另开实现任务。

阶段推进顺序、并行边界和子代理启用条件见 [07-stage-rollout-plan.md](./07-stage-rollout-plan.md)。本文档只描述单个 agent 接到任务后的执行规则。

## 1. 开发顺序

必须串行完成：

1. 阅读 [README.md](./README.md) 和 [00-api-blocking-node-skeleton.md](./00-api-blocking-node-skeleton.md)。
2. 确认所有 workflow 都使用 API blocking。
3. 确认所有 Start 节点只声明 `payload: json_object`。
4. 先搭通一个 workflow 的完整骨架：Start、normalize Code、If/Else、Template Transform、LLM、finalize Code、Variable Aggregator、End。
5. 再并行开发四个 workflow 的 Prompt 和校验逻辑。
6. 最后统一跑 blocking API 验收。

不得先并行深做 Prompt。通用输入、输出、兜底和校验口径未统一时，并行会导致字段漂移。

## 2. 可并行任务

在通用骨架确定后，可以并行：

| 子任务 | 输入文档 | 交付物 |
| --- | --- | --- |
| `intent_detection` | [01-intent_detection.md](./01-intent_detection.md) | 工作流节点、Prompt、finalize Code、成功/兜底测试 |
| `reply_generation` | [02-reply_generation.md](./02-reply_generation.md) | 工作流节点、Prompt、finalize Code、成功/兜底测试 |
| `conversation_segmentation` | [03-conversation_segmentation.md](./03-conversation_segmentation.md) | 工作流节点、Prompt、finalize Code、成功/兜底测试 |
| `user_profile_analysis` | [04-user_profile_analysis.md](./04-user_profile_analysis.md) | 工作流节点、Prompt、finalize Code、成功/兜底测试 |

并行时必须共享同一份：

- API 请求形态。
- Start 节点字段。
- Code outputs 类型约束。
- If/Else 输入门禁规则。
- Template Transform 上下文模板风格。
- Variable Aggregator 分支汇合方式。
- End outputs 字段名。
- 兜底输出。
- 临时字段禁用规则。

## 3. 单个 workflow 开发清单

每个 workflow agent 必须完成：

1. 配置 Start：只接收 `payload`。
2. 配置 normalize Code：提取 payload，输出 LLM 输入、`has_required_context` 和兜底字段。
3. 配置 If/Else：输入不足时跳过 LLM，直接进入汇合节点。
4. 配置 Template Transform：把消息、摘要、画像和 schema 规则拼成 LLM 上下文。
5. 配置 LLM：只输出 JSON。
6. 配置 finalize Code：解析、校验、兜底。
7. 配置 Variable Aggregator：优先合并 finalize 输出，兜底合并 normalize 输出。
8. 配置 End：只声明正式字段。
9. 用成功样例运行 blocking API。
10. 用空输入或弱相关样例运行 blocking API。
11. 人工检查 `data.outputs` 中没有临时字段。

## 4. Dify 配置注意事项

- Code 节点语言只能用 `python3` 或 `javascript`。
- Code 节点 outputs 必须和 `main()` 返回 key 完全一致。
- Code 只承担 normalize 和 finalize 两类职责；上下文拼装优先放到 Template Transform。
- Variable Aggregator 只做分支变量汇合，不做 `payload` 字段提取。
- Parameter Extractor 只用于自然语言抽参，不用于解析业务 JSON。
- End outputs 不要声明 `result`、`output`、`text`。
- LLM 节点不要把解释、分析过程、Markdown 包裹或代码块输出到最终 JSON。
- 如果模型容易输出代码块，finalize Code 必须能剥离 ```json 包裹后再解析。
- `reply_generation` 的 `reply.content` 是唯一可发送正文；其他 workflow 不能输出最终正文。

## 5. Blocking API 验收命令形态

后续 agent 可以用任意 HTTP 客户端调用，但请求体必须符合：

```json
{
  "inputs": {
    "payload": {
      "chatid": "CHAT_A"
    }
  },
  "response_mode": "blocking",
  "user": "dify-development-agent"
}
```

验收结果必须检查：

```json
{
  "data": {
    "status": "succeeded",
    "outputs": {}
  }
}
```

`outputs` 必须是正式业务字段结构。

## 6. 与自建系统开发的衔接

后续自建系统 agent 需要根据本文档调整或确认：

- `DifyHttpClient` 调用真实 Dify 时，把业务输入包装为 `inputs.payload`。
- `ai_workflows.input_schema` 仍校验业务 payload 本身，不校验 Dify API 外层包装。
- `ai_workflows.output_schema` 仍校验 Dify `data.outputs` 解析后的业务 JSON。
- Webhook 模式暂不作为主链路；如重新启用，必须先设计异步结果回收。

## 7. 完成定义

一个 workflow 可以交给自建系统接入前，必须满足：

- 成功样例通过。
- 兜底样例通过。
- LLM 非 JSON 输出时可被 finalize Code 兜底。
- 所有 `msgid` 引用来自输入。
- 所有 `userid` 引用来自输入。
- Dify 不写库、不发企微、不处理重试。
- 输出中没有 `cs`、`huihua`、`ceshiziduan`、`result_text`。
