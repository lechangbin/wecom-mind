# 项目文档索引

## 1. 项目简介

本项目是一个基于自建系统 + Dify 的企业微信智能机器人课程项目。

系统通过企业微信能力获取群聊消息，由自建系统负责消息入库、@ 识别、关键词扫描、调度、结果校验和企微发送，由 Dify 负责 AI 工作流，包括意图识别与动作决策、通用回复生成、会话切分和用户画像生成与更新。

## 2. 阅读顺序

建议按以下顺序阅读：

1. [架构总览](./architecture/README.md)
2. [需求与模块设计](./architecture/requirements.md)
3. [数据模型设计](./architecture/data-model.md)
4. [接口契约设计](./architecture/api-contracts.md)
5. [Dify 功能开发指引](./dify-development/README.md)
6. [模块实现文档索引](./modules/README.md)

## 3. 架构文档

- [架构总览](./architecture/README.md)
- [需求与模块设计](./architecture/requirements.md)
- [企微与 Dify 集成设计](./architecture/integration-and-workflows.md)
- [数据模型设计](./architecture/data-model.md)
- [接口契约设计](./architecture/api-contracts.md)
- [团队协作与分工边界](./architecture/team-collaboration.md)
- [Dify 功能开发指引](./dify-development/README.md)
- [待确认事项](./architecture/open-decisions.md)

## 4. 模块实现文档

- [配置与基础设施模块](./modules/00-foundation.md)
- [企微接入模块](./modules/01-wecom-adapter.md)
- [消息处理模块](./modules/02-message-ingestion.md)
- [触发路由模块](./modules/03-trigger-router.md)
- [Dify 调用模块](./modules/04-dify-gateway.md)
- [发送模块](./modules/05-outbound-dispatcher.md)
- [会话智能模块](./modules/06-conversation-intelligence.md)
- [用户画像模块](./modules/07-user-profile.md)
- [管理与统计模块](./modules/08-admin-analytics.md)

## 5. 当前核心决策

- 主体系统由 agent 辅助开发。
- Dify 工作流由团队成员分工完成。
- 自建系统是主状态中心。
- Dify 只返回结构化 JSON。
- 自建系统负责校验、写库和发送企微消息。
- MVP 阶段不建设 Internal MCP Server。
- 前端暂时不是重点，只预留统计和查询接口。

## 6. MVP 核心功能

- 企业微信回调接收。
- 群消息拉取或接收入库。
- @ 消息回复。
- 意图识别与主动提醒。
- 智能会话切分。
- 用户画像生成与更新。
- Dify 调用日志。
- 基础查询和统计接口。
