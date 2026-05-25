# 待确认事项

本文档记录当前架构中需要后续确认的问题。未确认事项不阻塞架构主方向，但会影响具体接口和实现细节。

## 1. 企业微信 MCP 回调形态

问题：

- 企微 MCP/webhook 回调中是否直接携带完整群消息内容？
- 还是只携带事件、游标、时间范围，需要自建系统再次调用接口拉取消息？

影响：

- 如果直接带完整消息，Message Puller 可以简化。
- 如果只带游标，必须建设 Pull Cursor 和 Pull Job。

当前架构处理：

- 同时兼容两种模式。
- MVP 实现时根据实际企微文档和账号权限选择。

## 2. 流式回复能力

问题：

- 当前企业微信智能机器人账号是否支持流式回复？
- 流式回复是否必须通过长连接 frame 的 req_id？
- webhook 模式是否支持同等流式能力？

影响：

- 如果支持，@ 回复场景下的 `reply_generation` 优先 streaming。
- 如果不支持，降级为普通 markdown/text 回复。

当前架构处理：

- 发送层抽象为 Outbound Dispatcher。
- `reply_generation` 工作流先支持 blocking 调试，后续支持 streaming 和 blocking 两种模式。

## 3. 引用原消息回复表现

问题：

- 机器人回复能否在企微客户端表现为“引用原消息回复”？
- 是通过透传原始 frame 实现，还是需要额外参数？

影响：

- 影响用户体验。
- 不影响消息入库和 AI 回复功能。

当前架构处理：

- 保存 `source_msgid`、`req_id`、`quote`、`reply_id`。
- 发送时优先使用原始 frame 或企微支持的回复能力。
- 真实环境验证后再补充具体接口参数。

## 4. Dify 部署方式

问题：

- 使用 Dify 云服务还是自部署 Dify？
- Dify 是否允许访问内网数据库或内网 API？

影响：

- 决定 Dify Gateway 的网络访问方式。
- 决定是否需要公网 API、网关鉴权或内网穿透。

当前架构处理：

- 默认自建系统主动调用 Dify。
- Dify 不直接写核心业务表。
- Dify 如需查数据，优先使用自建系统提供的只读 API。

## 5. Dify 是否直接读库

问题：

- 是否允许 Dify 使用只读账号查询数据库？

影响：

- 允许直接读库可以减少自建查询 API。
- 但权限、脱敏、审计能力较弱。

当前建议：

- MVP 阶段优先由自建系统组装上下文 JSON。
- 若 Dify 需要补充查询，开放受控只读 REST API。
- 暂不建设 Internal MCP Server。

## 6. 技术栈

问题：

- 后端使用 Python、Node.js、Java 还是其他语言？
- 任务队列使用 Redis/RQ、Celery、BullMQ、Sidekiq 还是数据库任务表？
- 数据库使用 MySQL、PostgreSQL 还是其他？

影响：

- 影响开发实现和部署。
- 不影响当前业务架构。

当前建议：

- 如果重视企微智能机器人 SDK，Node.js/TypeScript 更顺手。
- 如果重视 AI 和数据处理，Python/FastAPI 更顺手。
- 也可以采用 Python 主系统 + Node.js 机器人适配器的组合，但 MVP 不建议拆太复杂。

## 7. 前端范围

问题：

- 前端是否只做课程展示，还是要作为真实管理后台？

影响：

- 影响权限、页面、统计宽表和查询性能。

当前架构处理：

- 前端不是当前重点。
- 先保留管理与统计接口。
- MVP 页面建议只做总览、群聊分析、会话摘要、用户画像、AI 运行日志。

## 8. Dify 异步结果回收状态机

问题：

- Dify webhook 是否必须采用“先返回 ack，后续再回传结果”的异步模式？
- 自建系统是否需要为 Dify 调用建立完整的异步结果回收状态机？

当前代码判断：

- 项目已有状态基础，但不是完整的 Dify 异步状态机。
- `ai_runs` 已记录 `pending/running/success/failed/invalid_output`、输入输出、开始时间、结束时间和错误信息。
- `message_ingestion_jobs`、`outbox_messages` 已有任务处理、状态流转和 `retry_count`，说明系统具备扩展异步任务的基础。
- 但 Dify 异步回收仍缺少关键能力：外部 Dify run id、correlation id、异步回调入口、回调鉴权、超时扫描、结果幂等落库、失败重试策略。

当前建议：

- MVP 阶段不立即实现 Dify 异步结果回收。
- 当前 Dify 主链路优先使用 webhook 同步返回正式业务 outputs，由 Dify Gateway 在一次调用内完成 JSON 校验和落库。
- 如果 Dify webhook 只能返回 `{"status":"success","message":"Webhook processed successfully"}` 这类 ack，则视为尚未拿到业务结果，当前阶段应标记为失败并暴露配置问题，而不是假装成功。

后续触发条件：

- Dify 工作流耗时经常超过企微或自建系统可接受的同步等待时间。
- `conversation_segmentation`、`user_profile_analysis` 变成长任务，适合后台处理。
- 需要 Dify 在工作流结束后主动回调自建系统写回结果。
- 需要对 Dify 调用做统一超时、重试、补偿和人工排查。

后续设计方向：

- 在 `ai_runs` 增加 `external_run_id`、`correlation_id`、`callback_received_at`、`timeout_at`、`retry_count` 等字段。
- 新增 Dify 结果回调接口，例如 `/api/dify/workflow-results`，只接收 Dify 的最终业务 JSON，不接收自然语言解释。
- 回调 payload 必须携带 `correlation_id` 或 `ai_run_id`、`workflow_code`、`status`、`outputs`，并通过签名或共享密钥鉴权。
- 新增超时扫描任务，把长期 `running` 的 `ai_runs` 标记为 `failed` 或 `timeout`。
- 继续保持边界：Dify 不直接写数据库、不发企微消息、不负责幂等和重试；这些仍由自建系统处理。
