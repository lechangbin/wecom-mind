# 07. 用户画像模块

旧用户画像工作流说明已移除。

后续需要重新基于以下事实确定功能边界：

- 数据库表：`user_profiles`、`user_profile_facts`、`messages`、`conversation_segments`、`ai_runs`。
- 现有系统能力：`src/app/profiles` 的用户选择、当前画像读取、证据校验、画像版本写入逻辑。
- 可选输入：用户近期消息、会话摘要、当前画像、统计信息、分析模式、时间范围等。

在新版 Dify 节点编排确认前，本文件不再声明具体 Dify 应用、workflow code、Prompt、节点编排或输出结构。
