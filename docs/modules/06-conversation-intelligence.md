# 06. 会话智能模块

旧会话切分工作流说明已移除。

后续需要重新基于以下事实确定功能边界：

- 数据库表：`conversation_segments`、`messages`、`ai_runs`。
- 现有系统能力：`src/app/conversations` 的请求字段、查询窗口、结果写入与校验逻辑。
- 可选输入：消息窗口、候选边界、上一段会话信息、参与人、时间范围等。

在新版 Dify 节点编排确认前，本文件不再声明具体 Dify 应用、workflow code、Prompt、节点编排或输出结构。
