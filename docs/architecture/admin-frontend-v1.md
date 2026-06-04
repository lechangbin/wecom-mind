# 管理员前端 V1 架构文档

本文档定义第一版管理员前端的功能边界、页面结构、数据流和接口依赖。V1 的目标不是完整运营后台，而是让开发者和管理员能看清真实企微消息、Dify 调用、outbox 发送、会话沉淀和用户画像链路，快速定位“为什么没回复、为什么回复错、为什么画像没更新”。

## 1. 目标

V1 管理员前端优先解决四类问题：

1. 运行状态可见：API、长连接 worker、补漏 worker、AI memory worker、Dify 调用是否正常。
2. 链路可追踪：一条消息从入库、触发、AI run、outbox 到发送结果可以串起来看。
3. AI 结果可审查：会话切分、画像更新、回复画像化是否按证据生成。
4. 实机测试可操作：能手动触发会话/画像全量测试，能查看最近错误和结果。

V1 不做：

- 复杂权限系统。
- 大屏式统计报表。
- 多租户。
- 工单系统。
- Dify 应用编辑器。
- 复杂重试、队列、Redis 配置管理。

## 2. 推荐技术形态

前端建议采用单页应用：

```text
React + TypeScript + Vite
```

理由：

- 项目后端已经是 FastAPI，前端可以独立开发、独立启动。
- V1 主要是表格、详情页、状态卡片和少量操作按钮，不需要 Next.js 这类 SSR 能力。
- TypeScript 有利于把后端返回结构固化成前端类型，减少字段错配。

部署形态：

```text
开发期：Vite dev server -> FastAPI API
本地集成：FastAPI 静态托管 dist 或单独启动前端
生产化后续：再决定是否拆分 nginx / 前后端独立部署
```

## 3. 信息架构

V1 建议 7 个主页面。

### 3.1 总览

目的：打开后台第一眼知道系统是否能用。

展示：

- 今日消息数、累计消息数。
- 活跃群聊数、活跃用户数。
- 今日触发次数。
- Dify 调用次数、成功率、平均耗时。
- pending/failed outbox 数量。
- 近 24 小时消息趋势。
- 工作流成功率概览。
- worker 状态摘要。

已有接口：

- `GET /health`
- `GET /api/dashboard/overview`
- `GET /api/stats/messages`
- `GET /api/stats/workflows`

缺口：

- 还没有统一 worker 状态接口。V1 可以先用日志和健康提示占位，后续补 `GET /api/system/workers`。

### 3.2 消息与群聊

目的：查看真实入库消息，确认 userid/msgid/chatid/sender_type 是否正确。

展示：

- 群列表。
- 群消息列表。
- 消息详情。
- 用户消息 / 机器人消息筛选。
- @ 消息筛选。
- msgid、userid、create_time、sender_type、mentioned_bot。

已有接口：

- `GET /api/chats`
- `GET /api/chats/{chatid}/messages`
- `GET /api/stats/messages`

缺口：

- 消息列表当前筛选能力偏弱，后续建议补 `userid`、`sender_type`、`mentioned_bot`、`start/end` 查询参数。
- 消息详情页可以先复用列表项，后续补 `GET /api/messages/{message_id}`。

### 3.3 AI 运行记录

目的：排查 Dify 输入输出、耗时、失败原因和 JSON 校验问题。

展示：

- AI run 列表。
- workflow_code、status、latency_ms、created_at。
- input_json 摘要。
- output_json。
- error_message。
- 一键跳转关联消息、outbox、会话或画像。

已有接口：

- `GET /api/ai-runs`
- `GET /api/ai-runs/{run_id}`
- `GET /api/stats/workflows`

缺口：

- 当前 ai_runs 详情没有统一返回关联对象，需要前端从 input/output 推断。后续建议补 `related_message_id`、`related_outbox_id`、`related_conversation_no` 等派生字段。

### 3.4 发送记录 Outbox

目的：确认机器人是否真的发送、失败在哪里、是否重复发送。

展示：

- outbox 列表。
- scene：reply / reply_recovery / proactive。
- status：pending / sending / sent / failed / canceled。
- chatid、target_userids、content、source_id。
- external_msgid、error_code、error_message、retry_count。
- 手动发送按钮。

已有接口：

- `GET /api/outbox-messages`
- `GET /api/outbox-messages/{outbox_id}`
- `POST /api/outbox-messages/{outbox_id}/send`

V1 操作限制：

- 只允许对 `pending/failed` 执行手动发送。
- 发送前弹确认，避免误发测试群。

### 3.5 会话沉淀

目的：查看消息如何被切成会话，为画像更新提供证据。

展示：

- conversation_segments 列表。
- conversation_no、chatid、起止消息、起止时间、participants、confidence、status。
- 会话详情内展示该段消息。
- 手动触发某天全量测试。

已有接口：

- `GET /api/conversations`
- `GET /api/conversations/{conversation_no}`
- `POST /api/ai-memory/full-test/run`

缺口：

- 会话详情目前没有直接返回段内消息，建议补 `GET /api/conversations/{conversation_no}/messages`。
- 手动运行结果需要展示 `chat_results`、`profile_results`，当前接口已返回。

### 3.6 用户画像

目的：查看画像是否来自正确证据，并评估画像是否能用于回复个性化。

展示：

- 用户列表。
- 用户详情。
- 当前画像 summary。
- facts：fact_type、label、description、confidence、status。
- evidence_msgids、evidence_conversation_nos。
- 画像版本历史。

已有接口：

- `GET /api/users`
- 历史 `GET /api/users/{userid}/profile` 文件存在，但当前主应用未挂载，V1 需要重新接入或重建。

缺口：

- 需要补新版用户画像详情接口，建议：

```http
GET /api/users/{userid}/profile
GET /api/users/{userid}/profile/versions
```

V1 不做人工审核流，只做查看和排查。人工审核、回滚、版本对比放到 v0.4。

### 3.7 实机测试面板

目的：把测试动作集中到一个页面，减少从终端、日志、数据库之间来回跳。

展示：

- 当前配置摘要：Dify real/mock、sender mode、补漏开关、AI memory 开关。
- 最近 20 条 AI run。
- 最近 20 条 failed outbox。
- 最近一次 AI memory full-test 结果。
- 手动触发：
  - 指定日期和 chatid 运行 `POST /api/ai-memory/full-test/run`。
  - 指定 outbox 手动发送。

已有接口：

- `GET /health`
- `POST /api/ai-memory/full-test/run`
- `GET /api/ai-runs`
- `GET /api/outbox-messages`

缺口：

- 需要补配置摘要接口，建议 `GET /api/system/config-summary`，只返回脱敏字段和开关，不返回密钥。
- 需要补 worker 状态接口，建议 `GET /api/system/workers`。

## 4. 页面导航

建议导航结构：

```text
总览
消息与群聊
AI 运行记录
发送记录
会话沉淀
用户画像
实机测试
```

页面关系：

```text
消息详情 -> AI 运行记录 -> Outbox -> 发送结果
消息列表 -> 会话沉淀 -> 用户画像 -> 回复画像化效果
总览 -> 错误卡片 -> AI run / outbox / worker 日志
实机测试 -> ai-memory full-test -> 会话沉淀和用户画像
```

## 5. 前端 Module 划分

建议前端保持以下 Module：

```text
api/
  httpClient
  dashboardApi
  chatsApi
  aiRunsApi
  outboxApi
  conversationsApi
  usersApi
  systemApi

features/
  dashboard
  messages
  aiRuns
  outbox
  conversations
  profiles
  liveTest

shared/
  Layout
  DataTable
  StatusBadge
  JsonViewer
  TimeRangePicker
  EmptyState
  ErrorPanel
```

设计原则：

- 每个页面只依赖对应 api Module。
- JSON 展示使用可折叠 viewer，默认折叠大字段。
- 表格需要分页，避免一次性拉大列表。
- 所有操作按钮必须显示确认状态和错误结果。

## 6. 数据流

### 6.1 排障链路

```text
用户在企微发消息
-> messages 入库
-> trigger_events / ai_runs
-> outbox_messages
-> 企微发送结果
-> 管理前端按 message / ai_run / outbox 串联查看
```

### 6.2 画像链路

```text
messages
-> ai-memory full-test
-> conversation_segments
-> user_profile_update ai_runs
-> user_profiles / user_profile_facts
-> 回复工作流 payload 注入画像
-> 管理前端查看画像证据与回复效果
```

## 7. V1 接口优先级

P0 已有或必须补齐：

- `GET /health`
- `GET /api/dashboard/overview`
- `GET /api/chats`
- `GET /api/chats/{chatid}/messages`
- `GET /api/ai-runs`
- `GET /api/ai-runs/{run_id}`
- `GET /api/outbox-messages`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_no}`
- `POST /api/ai-memory/full-test/run`
- `GET /api/users`
- `GET /api/users/{userid}/profile`

P1 建议补齐：

- `GET /api/conversations/{conversation_no}/messages`
- `GET /api/users/{userid}/profile/versions`
- `GET /api/system/config-summary`
- `GET /api/system/workers`

P2 后续：

- 人工审核画像。
- 回滚画像版本。
- 导出报表。
- 权限角色。
- Redis 缓存。

## 8. 界面风格

这是运维和开发排障工具，不是营销页面。

视觉原则：

- 信息密度适中，优先可扫描。
- 少用大面积装饰。
- 状态颜色清晰：success、running、pending、failed、invalid_output。
- 重要错误优先出现在总览和实机测试页。
- 不用“功能介绍卡片”堆页面，直接展示可操作数据。

## 9. 验收标准

V1 完成后应该能做到：

1. 打开总览能确认系统当天是否正常运行。
2. 能按群查看真实消息，确认 msgid 和 userid。
3. 能查看任意 AI run 的输入、输出、错误和耗时。
4. 能查看 outbox 是否已发送或失败。
5. 能看到会话切分结果，并追溯到原始消息。
6. 能看到用户画像和证据来源。
7. 能手动触发一次真实消息库的 AI memory full-test。
8. 出问题时能判断大致属于 Dify、企微发送、入库、画像、会话切分还是配置问题。

## 10. 第一阶段开发建议

第一阶段只做可用骨架和排障闭环：

1. 前端项目骨架、路由、布局。
2. 总览页。
3. 消息与群聊页。
4. AI 运行记录页。
5. Outbox 页。
6. 会话沉淀页。
7. 用户画像页先做只读。
8. 实机测试页接 `POST /api/ai-memory/full-test/run`。

可以并行开发：

- 页面骨架和共享组件。
- 消息/群聊页。
- AI run/outbox 页。
- 会话/画像页。

需要先串行确定：

- API response type。
- 分页和错误格式。
- 用户画像详情接口是否重新挂载。
- 系统配置摘要和 worker 状态接口是否进入 V1。
