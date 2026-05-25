# 企微与 Dify 集成设计

## 1. 集成原则

本项目有两个外部核心系统：企业微信和 Dify。企业微信负责消息入口和消息出口，Dify 负责 AI 推理。自建系统必须位于二者中间，作为状态中心、校验中心和审计中心。

核心原则：

- 自建系统负责确定性流程：消息采集、入库、触发判断、Dify 调用、结果校验、写库、发送、幂等和重试。
- Dify 负责不确定性 AI 任务：意图识别、回复生成、会话切分、用户画像演进。
- Dify 输出只作为结构化建议或回复内容，不能直接写库或发送企微消息。
- Dify 功能配置以 [Dify 功能开发指引](../dify-development/README.md) 为准。

## 2. 企业微信接入设计

### 2.1 接入通道分类

| 通道 | 用途 | 架构定位 |
| --- | --- | --- |
| 企微 MCP/webhook 回调 | 接收群消息通知或群消息数据 | 消息采集主入口 |
| 企微 MCP 拉取能力 | 拉取群内聊天记录 | 消息采集核心能力 |
| 智能机器人长连接 | 实时消息事件、流式回复、主动发送 | 实时交互和回复通道 |
| 普通群机器人 webhook | 低优先级通知 | 备用发送通道，不作为采集主入口 |

### 2.2 消息采集模式

当前设计兼容两种企微 MCP 回调形态。

模式 A：回调直接携带完整消息。

- Callback Gateway 接收回调。
- 校验来源。
- 保存原始 payload。
- 直接进入 Message Ingestion。

模式 B：回调只携带事件或游标。

- Callback Gateway 接收回调。
- 保存回调事件。
- 创建 `message_pull_job`。
- Puller 根据 chatid、cursor、time_range 或 event_id 拉取消息。
- 拉取结果进入 Message Ingestion。

### 2.3 触发边界

一个企业微信智能机器人实例只能选择一种主接入方式：URL 回调或长连接。接入方式只决定消息如何进入自建系统，不决定业务触发类型。

| 触发来源 | 判断位置 | 后续 Dify 工作流 |
| --- | --- | --- |
| 用户显式 @ 机器人 | 自建系统 Trigger Router | `reply_generation` |
| 关键词命中 | 自建系统调度器扫描消息窗口 | 作为 `intent_detection.matched_keywords` 输入 |
| 定时意图扫描 | 自建系统调度器扫描消息窗口 | `intent_detection` |
| 会话切分任务 | 自建系统调度器或管理员 | `conversation_segmentation` |
| 用户画像任务 | 自建系统调度器或管理员 | `user_profile_analysis` |
| 手动重跑 | 管理后台或内部任务 | 按目标 workflow 调用 |

关键词是召回线索，不是独立 AI 回复链路。`intent_detection` 判断是否需要处理；需要回复时，自建系统再调用 `reply_generation`。

### 2.4 消息发送模式

| 场景 | 推荐发送方式 | 是否流式 |
| --- | --- | --- |
| 用户 @ 机器人后的直接回复 | 智能机器人回复通道 | 后续可流式 |
| 意图识别后的主动提醒 | 主动消息或普通回复任务 | 不流式 |
| 会话摘要通知 | 主动消息或模板卡片 | 不流式 |
| 系统异常告警 | 普通 webhook 备用通道 | 不流式 |

所有可发送正文都必须来自 `reply_generation`，或来自自建系统固定模板。`intent_detection` 不能直接输出可发送正文。

## 3. Dify 通讯设计

### 3.1 Dify 定位

Dify 是 AI 子工作流引擎，不是系统主状态机。

Dify 负责：

- 意图识别与动作决策。
- 通用回复内容生成。
- 会话切分判断。
- 用户画像生成与更新。

Dify 不负责：

- 企微回调接收。
- @ 机器人触发判断。
- 关键词规则扫描。
- 核心业务表直接写入。
- 企微消息直接发送。
- 消息游标推进。
- 幂等和失败补偿。

### 3.2 工作流注册

自建系统调用 Dify 时统一使用工作流注册表。

工作流注册信息包括：

- `workflow_code`：内部稳定编码。
- `workflow_name`：显示名称。
- `dify_app_id`：Dify 应用 ID。
- `dify_workflow_id`：Dify 工作流 ID。
- `version`：内部版本号。
- `response_mode`：当前主链路为 `blocking`。
- `input_schema`：业务 payload JSON schema。
- `output_schema`：业务 outputs JSON schema。
- `enabled`：是否启用。

### 3.3 API blocking 主链路

当前 Dify 通讯主链路为 API blocking。自建系统调用：

```json
{
  "inputs": {
    "payload": {}
  },
  "response_mode": "blocking",
  "user": "system-or-operator"
}
```

约定：

- `payload` 是自建系统构造的业务输入 JSON。
- Dify Start 节点只声明 `payload: json_object`。
- Dify 返回的 `data.outputs` 必须是正式业务字段。
- 自建系统必须校验 outputs 后再写库或发送。

Webhook 不作为当前主链路。若后续重新启用 Webhook，必须先设计异步 ack 与最终 outputs 的回收和关联方式。

### 3.4 输入输出原则

输入原则：

- 自建系统提供足够上下文，减少 Dify 直接访问数据库。
- 输入中不传 access_token、corpsecret 等敏感信息。
- 复杂数组和对象都放在 `payload` 内。

输出原则：

- Dify 必须输出 JSON。
- `intent_detection` 只能输出动作建议和 `reply_instruction`，不能输出最终回复正文。
- `reply_generation` 是唯一输出可发送消息正文的工作流。
- 会话和画像类工作流输出只作为建议。
- 自建系统必须校验 schema、`msgid`、`userid`、`chatid` 和 `confidence`。
- Dify 输出不能直接落核心表。

## 4. 核心链路

### 4.1 @ 回复链路

1. 企微消息进入自建系统。
2. Message Ingestion 标准化消息并识别 `mentioned_bot`。
3. Trigger Router 判断这是显式 @ 机器人。
4. 自建系统构造 `reply_generation` payload。
5. 自建系统通过 Dify API blocking 调用 `reply_generation`。
6. Dify 返回最终回复 JSON。
7. 自建系统校验输出。
8. Outbound Dispatcher 创建并发送企微回复。

### 4.2 关键词和定时意图链路

1. 消息先完成入库。
2. 调度器按群、时间窗口和关键词规则扫描消息。
3. 自建系统构造 `intent_detection` payload。
4. Dify 返回动作建议。
5. 自建系统校验动作、证据和目标用户。
6. 如果动作需要回复，自建系统调用 `reply_generation`。
7. 自建系统校验回复输出并创建 outbox。

### 4.3 会话切分链路

1. 调度器或管理员选择消息窗口。
2. 自建系统构造 `conversation_segmentation` payload。
3. Dify 返回会话切分建议。
4. 自建系统校验边界、参与人和摘要。
5. 校验通过后写入会话表。

### 4.4 用户画像链路

1. 调度器或管理员选择目标用户。
2. 自建系统读取用户近期消息、参与会话摘要和当前画像。
3. 自建系统构造 `user_profile_analysis` payload。
4. Dify 返回画像生成或更新建议。
5. 自建系统校验证据、fact_id 和置信度。
6. 校验通过后写入画像事实并生成新画像版本。

## 5. 异常处理

### 5.1 企微回调异常

- 签名错误：拒绝并记录安全日志。
- 重复回调：根据幂等键忽略。
- payload 无法解析：保存原文并标记失败。
- 回调处理超时风险：先落库，再异步处理。

### 5.2 企微拉取异常

- 接口超时：重试。
- 游标失效：创建补偿拉取任务。
- 部分消息解析失败：成功消息继续入库，失败消息单独记录。

### 5.3 Dify 调用异常

- HTTP 请求失败：记录 `ai_runs.failed`。
- `data.status` 非 `succeeded`：记录失败原因。
- outputs 非 JSON：记录 `invalid_output`。
- schema 不合法：记录 `invalid_output`，不写业务表。
- 置信度过低：结果进入忽略或待确认状态。
- `intent_detection` 输出回复正文：视为非法输出。
- `reply_generation` 引用不存在的证据消息：视为非法输出。

### 5.4 发送异常

- 企微限流：延迟重试。
- 用户无权限或不在群内：标记失败，不无限重试。
- 发送成功但无回执：记录待确认状态，后续人工或任务检查。

## 6. 日志与审计

必须记录：

- 回调接收日志。
- 消息拉取日志。
- 消息入库日志。
- 触发规则命中日志。
- Dify 调用日志。
- Dify 输出校验日志。
- 发送日志。
- 会话切分写入日志。
- 用户画像更新日志。

日志不得记录：

- 企微 secret。
- Dify API key。
- access_token。
- 未脱敏的敏感配置。

