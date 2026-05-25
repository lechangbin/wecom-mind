# 04. Dify 调用模块

## 1. 模块职责

Dify 调用模块是自建系统和 Dify 之间的唯一通道。

负责：

- 管理工作流注册信息。
- 构造业务输入 JSON。
- 通过 Dify API blocking 调用工作流。
- 保存 `ai_runs`。
- 解析和校验 Dify `data.outputs`。
- 将合法输出交给对应业务处理模块。

不负责：

- Dify Prompt 和节点编排。
- 企业微信消息发送。
- 会话、画像等业务表直接写入。
- Dify Webhook 异步结果回收。

具体 Dify 节点配置、Prompt、兜底和验收样例见 [Dify 功能开发指引](../dify-development/README.md)。

## 2. 调用模式

当前主链路为 Dify API blocking：

```http
POST /v1/workflows/run
Authorization: Bearer <DIFY_API_KEY>
Content-Type: application/json
```

请求体固定为：

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

- `payload` 是自建系统构造的业务 JSON。
- `ai_workflows.input_schema` 校验的是业务 JSON，不是 Dify API 外层包装。
- Dify Start 节点只接收 `payload: json_object`。
- Webhook 不作为本阶段主链路；如后续启用，必须先补结果回收机制。
- `reply_generation` 后续可在实时 @ 回复场景扩展 streaming，但 blocking 是当前调试和验收基线。

## 3. 核心输入

调用模块内部接收：

```json
{
  "workflow_code": "reply_generation",
  "workflow_version": "v1",
  "trigger_event_id": 40001,
  "response_mode": "blocking",
  "input_json": {}
}
```

`input_json` 会被包装为 Dify API 的 `inputs.payload`。

## 4. 核心输出

`ai_runs` 状态：

- `pending`
- `running`
- `success`
- `failed`
- `invalid_output`

Dify API 成功响应中只消费：

```json
{
  "data": {
    "status": "succeeded",
    "outputs": {}
  }
}
```

`outputs` 必须是正式业务字段，例如：

- `intent_detection`：`actions`
- `reply_generation`：`action`、`reply`、`metadata`、`confidence`
- `conversation_segmentation`：`segments`
- `user_profile_analysis`：`userid`、`profile_action`、`facts_to_add` 等

## 5. 工作流注册

工作流注册表：`ai_workflows`

必须包含：

- `workflow_code`
- `version`
- `dify_app_id`
- `dify_workflow_id`
- `response_mode`
- `input_schema`
- `output_schema`
- `enabled`

`dify_webhook_url` 可保留为兼容字段，但本阶段不作为主调用路径。

## 6. 处理流程

### 6.1 API blocking 调用

1. 读取工作流配置。
2. 校验业务 `input_json`。
3. 创建 `ai_runs`，状态为 `running`。
4. 调用 Dify `/v1/workflows/run`，将业务输入包装为 `inputs.payload`。
5. 检查 HTTP 状态码。
6. 检查 `data.status=succeeded`。
7. 解析 `data.outputs`。
8. 保存输出 JSON。
9. 校验输出 schema 和业务引用。
10. 更新 `ai_runs.status`。
11. 将合法输出交给业务结果处理器。

### 6.2 Webhook

Webhook 不纳入当前主链路。

如果后续重新启用，必须先设计：

- webhook ack 与最终 outputs 的关联方式。
- Dify 运行记录查询或回调机制。
- `ai_runs` 从 `running` 到最终状态的更新策略。
- 超时和失败补偿。

在没有结果回收机制前，不允许把异步 ack 当作业务 outputs。

### 6.3 Streaming

Streaming 暂只作为 `reply_generation` 的后续扩展方向：

1. 创建 `ai_runs`。
2. 调用 Dify streaming。
3. 将增量文本传给发送层。
4. 拼接完整回复。
5. 保存最终输出。
6. 更新运行状态。

## 7. 输出校验

必须校验：

- JSON 可解析。
- 必填字段存在。
- 字段类型正确。
- `confidence` 在 0 到 1。
- 输出引用的 `msgid` 来自输入上下文。
- 输出引用的 `userid` 来自输入上下文。
- `intent_detection` 没有最终回复正文。
- `reply_generation` 的 `reply.content` 非空且可发送。

校验失败：

- `ai_runs.status=invalid_output`
- 不写业务表。
- 不创建 outbox。
- 记录错误原因。

## 8. MVP 实现范围

必须实现：

- 工作流注册读取。
- Dify API blocking 调用。
- `inputs.payload` 包装。
- `ai_runs` 日志。
- Dify outputs 解析。
- 输出 JSON schema 校验。
- 业务引用校验。

可延后：

- Dify Webhook 异步结果回收。
- token 用量统计。
- 多 Dify endpoint。
- 工作流灰度发布。
- `reply_generation` streaming。

## 9. 验收标准

- 能按 `workflow_code` 找到正确工作流配置。
- 能调用 Dify `/v1/workflows/run` 并保存输入输出。
- 发送给 Dify 的业务输入位于 `inputs.payload`。
- Dify 返回非法 JSON 或非法业务字段时进入 `invalid_output`。
- Dify 调用失败时进入 `failed` 并记录错误。
- 合法输出能进入对应业务处理模块。

