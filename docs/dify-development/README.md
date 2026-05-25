# Dify 功能开发指引

本目录是当前项目 Dify 功能开发的唯一实现入口。旧的 Webhook 主链路文档不再作为 Dify 功能配置依据。

本阶段只约束 Dify 工作流的输入、节点编排、输出和验收方式，不要求 Dify 写业务库、发送企业微信消息、处理幂等、处理重试或承担自建系统状态机职责。

## 当前架构决策

| 项目 | 决策 |
| --- | --- |
| 主调用方式 | Dify API blocking |
| API 路径 | `/v1/workflows/run` |
| Dify Start 输入 | 只声明一个 `payload` |
| `payload` 类型 | `json_object` |
| Dify 内部路线 | 官方节点优先：`Start -> Code 最小规范化 -> If/Else -> Template Transform -> LLM -> Code JSON 校验与兜底 -> Variable Aggregator -> End` |
| Webhook | 不作为本阶段主链路；异步结果回收后续再设计 |
| 输出格式 | End outputs 直接是正式业务字段 |

自建系统调用 Dify 时固定使用：

```json
{
  "inputs": {
    "payload": {}
  },
  "response_mode": "blocking",
  "user": "system-or-operator"
}
```

Dify Start 节点只声明：

```json
{
  "variable": "payload",
  "type": "json_object",
  "required": true
}
```

## 文档清单

| 文档 | 用途 |
| --- | --- |
| [architecture-design.html](./architecture-design.html) | Dify 功能架构设计的可视化 HTML 展示页 |
| [00-api-blocking-node-skeleton.md](./00-api-blocking-node-skeleton.md) | 通用 API blocking 契约、节点骨架、输出校验规则 |
| [01-intent_detection.md](./01-intent_detection.md) | 意图识别与动作决策工作流 |
| [02-reply_generation.md](./02-reply_generation.md) | 通用回复生成工作流 |
| [03-conversation_segmentation.md](./03-conversation_segmentation.md) | 智能会话切分工作流 |
| [04-user_profile_analysis.md](./04-user_profile_analysis.md) | 用户画像生成与更新工作流 |
| [05-agent-development-guide.md](./05-agent-development-guide.md) | 后续 agent 分工、并行开发和验收流程 |
| [06-node-first-visual-composition.md](./06-node-first-visual-composition.md) | 官方节点优先的可视化编排和 Code 替换边界 |
| [07-stage-rollout-plan.md](./07-stage-rollout-plan.md) | Dify 功能从文档到实施的阶段推进预排 |

## 通用边界

Dify 负责：

- 根据自建系统提供的上下文做 AI 判断、生成或总结。
- 输出可由自建系统校验的 JSON。
- 在不确定、证据不足或输入不完整时返回兜底 JSON。

Dify 不负责：

- 企业微信消息拉取、回调接收或发送。
- 判断是否 @ 机器人。
- 关键词扫描、定时调度、幂等、重试。
- 写入核心业务表。
- 决定最终业务状态。

## Workflow 清单

| workflow_code | 功能 | 输出主字段 |
| --- | --- | --- |
| `intent_detection` | 判断消息窗口是否需要回复、建任务或忽略 | `actions` |
| `reply_generation` | 生成唯一可发送的企微回复正文 | `action`、`reply`、`metadata`、`confidence` |
| `conversation_segmentation` | 将消息窗口切分成主题会话建议 | `segments` |
| `user_profile_analysis` | 生成或更新用户画像建议 | `userid`、`profile_action`、`facts_to_add` 等 |

## 交付红线

- 最终 outputs 必须是合法 JSON 对象。
- 不要在 JSON 外输出解释性自然语言。
- 不允许输出临时字段：`cs`、`huihua`、`ceshiziduan`、`result_text`。
- 不要编造输入中不存在的 `msgid`。
- 不要编造输入中不存在的 `userid`。
- `intent_detection` 不能输出最终可发送回复正文。
- `reply_generation` 是唯一输出可发送消息正文的工作流。
