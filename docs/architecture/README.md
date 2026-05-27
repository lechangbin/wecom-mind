# 架构文档重建说明

旧 Dify 功能划分、旧工作流契约、旧节点编排和旧联调计划已移除。

后续 Dify 架构需要重新基于两类事实确定：

- 数据库模型与字段：以 [data-model.md](./data-model.md) 和 `src/app/db/models.py` 为准。
- 自建系统现有能力：以 `src/app` 下的服务、schema、API 和调用消费路径为准。

当前不要再引用旧的 Dify 四工作流文档、旧 DSL、旧 HTML 架构展示或旧节点配置。新版文档会在字段盘点和功能边界确认后重新建立。

当前新版 Dify AI 功能路线展示页：

- [Dify AI 功能重建架构](./dify-ai-architecture.html)
