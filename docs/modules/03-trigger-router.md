# 03. 触发路由模块

旧触发路由与 Dify workflow 映射已移除。

后续需要重新基于以下事实确定触发边界：

- 数据库表：`trigger_rules`、`trigger_events`、`messages`、`ai_workflows`、`ai_runs`。
- 现有系统能力：`src/app/triggers`、`src/app/scheduled_intents`、消息入库后的可用字段。
- 业务目标：哪些场景只产生触发事件，哪些场景需要进入 AI，哪些场景直接由自建系统处理。

在新版 Dify 功能边界确认前，本文件不再声明具体 Dify 应用、workflow code、Prompt、节点编排或输出结构。

## 已处理消息判定

主动提醒排除规则不得使用文本前缀判断。是否已经由 @ 回复处理，以 `trigger_events` 中同一 `message_id` 的 mention 规则记录为准。

`handled_records` 会传入主动提醒类 Dify payload，用于提示模型不要重复回答已经处理过的 @ 消息。

当前阶段的系统侧事实来源：

- `messages.mentioned_bot`：消息是否 @ 机器人。
- `trigger_events.status`：@ 触发是否已经进入处理流程并完成。
- `bot_replies` / `outbox_messages`：回复生成与发送审计。
- `messages.sender_type` / `messages.bot_role`：区分用户消息、回复机器人消息和意图采集机器人消息。

主动提醒扫描应优先读取 `sender_type = "user"` 的消息窗口，再结合 `trigger_events` 生成 `handled_records`。不要用“内容是否以 @ 开头”作为排除条件。
