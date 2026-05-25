# 01. 企微接入模块

## 1. 模块职责

企微接入模块负责企业微信相关的入口和出口适配。

负责：

- 接收企微 MCP/webhook 回调。
- 校验回调来源。
- 保存回调原始数据。
- 创建消息拉取任务。
- 调用企微能力拉取群消息。
- 适配智能机器人回复能力。
- 保存企微侧回执。

不负责：

- Dify 工作流调用。
- 消息语义分析。
- 会话切分。
- 用户画像。

## 2. 上下游关系

上游：

- 企业微信 MCP/webhook。
- 企业微信智能机器人长连接或回复接口。

下游：

- 消息处理模块。
- 发送模块。
- 任务调度模块。

## 3. 核心子能力

### 3.1 Callback Gateway

入口：

- `POST /api/wecom/callbacks/mcp`

处理流程：

1. 接收 query 和 body。
2. 校验签名、timestamp、nonce。
3. 生成 `callback_id`。
4. 写入 `wecom_mcp_callbacks`。
5. 判断回调是否包含完整消息。
6. 完整消息进入消息入库任务。
7. 游标类回调创建消息拉取任务。
8. 快速返回成功。

### 3.2 Message Puller

处理流程：

1. 读取待处理 `message_ingestion_jobs`。
2. 根据 chatid、cursor 或 time range 调用企微接口。
3. 保存拉取前后游标。
4. 将消息逐条发送到 Message Ingestion。
5. 更新任务状态。

### 3.3 AiBot Reply Adapter

负责：

- 普通回复。
- 流式回复。
- 主动发送群消息。
- 主动 @ 用户。
- 模板卡片可后置。

注意：

- @ 实时流式回复优先依赖原始 frame 或 req_id。
- 主动消息不依赖原始 frame。

## 4. 关键数据表

- `wecom_mcp_callbacks`
- `wecom_mcp_pull_cursors`
- `message_ingestion_jobs`
- `messages_raw`
- `outbox_messages`

## 5. MVP 实现范围

必须实现：

- 企微回调接口。
- 回调原始数据保存。
- 消息拉取任务模型。
- 至少一种消息入库路径：回调直接入库或拉取后入库。
- 普通消息发送。

优先验证：

- 企微 MCP 回调 payload 结构。
- 流式回复能力。
- 引用原消息表现。

可延后：

- 模板卡片。
- 附件下载和解密。
- 普通群机器人 webhook 备用通道。

## 6. 验收标准

- 能接收一条企微回调并写入回调表。
- 重复回调不会重复创建消息。
- 能将至少一种文本消息写入消息处理模块。
- 能向指定 chatid 发送一条普通消息。
- 回调接口不会同步等待 Dify。
