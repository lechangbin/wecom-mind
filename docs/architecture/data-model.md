# 数据模型设计

## 1. 设计原则

数据模型以“消息资产沉淀 + AI 结果可追踪 + 发送行为可审计”为核心。

关键原则：

- 原始数据和标准化数据分开保存。
- 外部回调、消息拉取、Dify 调用、发送动作都要有状态记录。
- Dify 输出不能直接覆盖核心业务表，必须保留运行记录和校验结果。
- 用户画像事实必须保留证据来源。
- 会话切分结果必须能重跑和版本化。

## 2. 核心实体

### 2.1 wecom_chats

保存企微会话信息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| chatid | varchar | 企微群聊 ID 或会话 ID |
| chattype | varchar | group / single |
| name | varchar | 群名称或会话名称 |
| source | varchar | mcp / aibot / webhook |
| status | varchar | active / inactive |
| last_message_at | datetime | 最近消息时间 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

唯一约束：

- `chatid`

### 2.2 wecom_users

保存企微用户信息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| userid | varchar | 企微 userid |
| name | varchar | 用户名称 |
| alias | varchar | 别名 |
| department | varchar | 部门信息 |
| status | varchar | active / inactive |
| last_active_at | datetime | 最近活跃时间 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

唯一约束：

- `userid`

### 2.3 wecom_mcp_callbacks

保存企微 MCP/webhook 回调。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| callback_id | varchar | 内部生成的回调 ID |
| event_type | varchar | 回调事件类型 |
| chatid | varchar | 会话 ID，可为空 |
| raw_query | json | query 参数 |
| raw_body | json/text | 原始 body |
| signature_valid | boolean | 签名是否通过 |
| idempotency_key | varchar | 幂等键 |
| status | varchar | received / processed / failed |
| error_message | text | 错误信息 |
| received_at | datetime | 接收时间 |
| processed_at | datetime | 处理时间 |

唯一约束：

- `idempotency_key`

### 2.4 wecom_mcp_pull_cursors

保存群消息拉取游标。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| chatid | varchar | 会话 ID |
| cursor_type | varchar | seq / time / vendor_cursor |
| cursor_value | varchar | 当前游标 |
| last_pulled_at | datetime | 最近拉取时间 |
| status | varchar | active / paused / failed |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

唯一约束：

- `chatid, cursor_type`

### 2.5 message_ingestion_jobs

保存消息拉取和入库任务。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| job_type | varchar | pull / normalize / replay |
| chatid | varchar | 会话 ID |
| callback_id | varchar | 来源回调 ID |
| cursor_before | varchar | 执行前游标 |
| cursor_after | varchar | 执行后游标 |
| status | varchar | pending / running / success / failed |
| retry_count | int | 重试次数 |
| error_message | text | 错误信息 |
| created_at | datetime | 创建时间 |
| started_at | datetime | 开始时间 |
| finished_at | datetime | 完成时间 |

### 2.6 messages_raw

保存企微原始消息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| source | varchar | mcp / aibot / webhook |
| external_msgid | varchar | 企微消息 ID |
| req_id | varchar | 回调帧请求 ID |
| chatid | varchar | 会话 ID |
| userid | varchar | 发送人 |
| msgtype | varchar | 消息类型 |
| raw_payload | json/text | 原始消息 |
| idempotency_key | varchar | 幂等键 |
| create_time | datetime | 消息产生时间 |
| received_at | datetime | 系统接收时间 |

唯一约束：

- `source, external_msgid`
- 或 `idempotency_key`

### 2.7 messages

保存标准化消息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| raw_message_id | bigint | 对应原始消息 |
| external_msgid | varchar | 企微消息 ID |
| chatid | varchar | 会话 ID |
| chattype | varchar | group / single |
| userid | varchar | 发送人 userid |
| msgtype | varchar | text / image / file / voice / mixed |
| content_text | text | 文本内容 |
| normalized_content | json | 标准化内容 |
| quote_message | json | 企微 quote 字段 |
| quote_msgid | varchar | 引用消息 ID，可为空 |
| mentioned_bot | boolean | 是否 @ 机器人 |
| mentioned_users | json | 被 @ 用户 |
| create_time | datetime | 消息产生时间 |
| created_at | datetime | 入库时间 |

索引：

- `chatid, create_time`
- `userid, create_time`
- `mentioned_bot, create_time`
- `msgtype, create_time`

### 2.8 trigger_rules

保存触发规则。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| rule_code | varchar | 规则编码 |
| rule_name | varchar | 规则名称 |
| trigger_type | varchar | mention / keyword / schedule |
| config | json | 规则配置 |
| workflow_code | varchar | 关联 Dify 工作流 |
| priority | int | 优先级 |
| enabled | boolean | 是否启用 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 2.9 trigger_events

保存触发命中记录。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| rule_code | varchar | 命中规则 |
| trigger_type | varchar | 触发类型 |
| message_id | bigint | 来源消息 |
| chatid | varchar | 会话 ID |
| userid | varchar | 触发用户 |
| workflow_code | varchar | 目标工作流 |
| reason | json | 命中原因 |
| status | varchar | pending / handled / ignored / failed |
| created_at | datetime | 创建时间 |

唯一约束：

- `rule_code, message_id`

### 2.10 ai_workflows

保存 Dify 工作流注册信息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| workflow_code | varchar | 内部工作流编码 |
| workflow_name | varchar | 工作流名称 |
| provider | varchar | dify |
| dify_app_id | varchar | Dify 应用 ID |
| dify_workflow_id | varchar | Dify 工作流 ID |
| version | varchar | 内部版本 |
| response_mode | varchar | blocking / streaming |
| input_schema | json | 输入 schema |
| output_schema | json | 输出 schema |
| enabled | boolean | 是否启用 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

唯一约束：

- `workflow_code, version`

### 2.11 ai_runs

保存每次 Dify 调用记录。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| run_id | varchar | 内部运行 ID |
| workflow_code | varchar | 工作流编码 |
| workflow_version | varchar | 工作流版本 |
| trigger_event_id | bigint | 触发事件 ID，可为空 |
| input_json | json | 输入参数 |
| output_json | json | 输出结果 |
| response_mode | varchar | blocking / streaming |
| status | varchar | pending / running / success / failed / invalid_output |
| latency_ms | int | 耗时 |
| token_usage | json | token 用量，可为空 |
| error_message | text | 错误信息 |
| created_at | datetime | 创建时间 |
| started_at | datetime | 开始时间 |
| finished_at | datetime | 完成时间 |

唯一约束：

- `run_id`

### 2.12 bot_replies

保存机器人回复。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| reply_id | varchar | 内部回复 ID |
| source_message_id | bigint | 被回复消息 |
| ai_run_id | bigint | 来源 AI 调用 |
| chatid | varchar | 会话 ID |
| userid | varchar | 触发用户 |
| reply_type | varchar | text / markdown / stream / template_card |
| content | text | 回复内容 |
| stream_id | varchar | 流式消息 ID |
| external_msgid | varchar | 企微返回消息 ID |
| status | varchar | pending / sent / failed |
| created_at | datetime | 创建时间 |
| sent_at | datetime | 发送时间 |

### 2.13 outbox_messages

保存待发送和已发送消息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| outbox_id | varchar | 内部发送 ID |
| scene | varchar | reply / proactive / alert / summary |
| chatid | varchar | 目标会话 |
| target_userids | json | @ 或接收用户 |
| msgtype | varchar | markdown / text / template_card / media |
| content | json | 发送内容 |
| source_type | varchar | trigger / ai_run / manual / system |
| source_id | varchar | 来源 ID |
| status | varchar | pending / sending / sent / failed / canceled |
| retry_count | int | 重试次数 |
| external_msgid | varchar | 企微消息 ID |
| error_code | varchar | 错误码 |
| error_message | text | 错误信息 |
| scheduled_at | datetime | 计划发送时间 |
| sent_at | datetime | 实际发送时间 |
| created_at | datetime | 创建时间 |

唯一约束：

- `outbox_id`

### 2.14 conversation_segments

保存会话切分结果。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| conversation_no | varchar | 会话编号 |
| chatid | varchar | 会话所在群 |
| start_message_id | bigint | 起始消息 |
| end_message_id | bigint | 结束消息 |
| start_time | datetime | 起始时间 |
| end_time | datetime | 结束时间 |
| title | varchar | 会话标题 |
| summary | text | 会话摘要 |
| keywords | json | 关键词 |
| participants | json | 参与人 userid |
| ai_run_id | bigint | 来源 AI 调用 |
| confidence | decimal | 置信度 |
| version | int | 版本 |
| status | varchar | active / superseded / rejected |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

唯一约束：

- `conversation_no, version`

### 2.15 user_profiles

保存用户画像快照。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| userid | varchar | 用户 ID |
| profile_json | json | 画像快照 |
| summary | text | 画像摘要 |
| confidence | decimal | 总体置信度 |
| version | int | 版本 |
| last_analyzed_at | datetime | 最近分析时间 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

唯一约束：

- `userid, version`

### 2.16 user_profile_facts

保存画像事实。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint | 内部主键 |
| userid | varchar | 用户 ID |
| fact_type | varchar | interest / personal_info / preference / role / risk |
| label | varchar | 标签 |
| description | text | 描述 |
| evidence_msgids | json | 证据消息 ID |
| evidence_conversation_nos | json | 证据会话编号 |
| confidence | decimal | 置信度 |
| source_ai_run_id | bigint | 来源 AI 调用 |
| status | varchar | active / ignored / expired |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

索引：

- `userid, fact_type`
- `confidence`

## 3. 统计预留字段

为了后续前端统计，以下字段必须在设计中保留：

- `chatid`
- `userid`
- `msgtype`
- `create_time`
- `mentioned_bot`
- `trigger_type`
- `workflow_code`
- `workflow_version`
- `latency_ms`
- `status`
- `confidence`
- `token_usage`
- `retry_count`
- `error_code`

## 4. 数据保留建议

课程项目阶段：

- 原始消息永久保留或至少保留整个演示周期。
- AI 调用输入输出保留。
- 发送日志保留。
- 用户画像保留版本。

生产化阶段可再增加：

- 原始消息归档。
- 敏感字段脱敏。
- 分区表。
- 冷热数据分层。
