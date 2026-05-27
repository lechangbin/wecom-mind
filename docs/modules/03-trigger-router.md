# 03. 触发路由模块

旧触发路由与 Dify workflow 映射已移除。

后续需要重新基于以下事实确定触发边界：

- 数据库表：`trigger_rules`、`trigger_events`、`messages`、`ai_workflows`、`ai_runs`。
- 现有系统能力：`src/app/triggers`、`src/app/scheduled_intents`、消息入库后的可用字段。
- 业务目标：哪些场景只产生触发事件，哪些场景需要进入 AI，哪些场景直接由自建系统处理。

在新版 Dify 功能边界确认前，本文件不再声明具体 Dify 应用、workflow code、Prompt、节点编排或输出结构。
