# 项目文档索引

## 1. 项目简介

本项目是一个基于自建系统、企业微信能力和 Dify 的企业微信智能机器人课程项目。

当前 Dify 功能划分和节点编排正在重建。旧 Dify 功能开发文档已移除，后续以数据库模型和自建系统实际可选字段重新确定输入输出约束与功能边界。

## 2. 阅读顺序

建议按以下顺序阅读：

1. [架构总览](./architecture/README.md)
2. [数据模型设计](./architecture/data-model.md)
3. [模块实现文档索引](./modules/README.md)

## 3. 架构文档

- [架构总览](./architecture/README.md)
- [Dify AI 功能重建架构](./architecture/dify-ai-architecture.html)
- [数据模型设计](./architecture/data-model.md)
- [管理员前端 V1 架构](./architecture/admin-frontend-v1.md)

## 4. 模块实现文档

- [配置与基础设施模块](./modules/00-foundation.md)
- [企微接入模块](./modules/01-wecom-adapter.md)
- [消息处理模块](./modules/02-message-ingestion.md)
- [触发路由模块](./modules/03-trigger-router.md)
- [发送模块](./modules/05-outbound-dispatcher.md)
- [会话智能模块](./modules/06-conversation-intelligence.md)
- [用户画像模块](./modules/07-user-profile.md)
- [管理与统计模块](./modules/08-admin-analytics.md)

## 5. 当前核心决策

- 主体系统由 agent 辅助开发。
- 自建系统是主状态中心。
- Dify 功能边界待按数据库和自建系统字段重新定义。
- MVP 阶段不建设 Internal MCP Server。
- 前端暂时不是重点，只预留统计和查询接口。

## 6. MVP 核心功能

- 企业微信回调接收。
- 群消息拉取或接收入库。
- @ 消息回复。
- 基础查询和统计接口。
