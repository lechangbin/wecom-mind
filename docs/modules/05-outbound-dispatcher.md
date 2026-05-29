# 05. 发送模块

## 1. 模块职责

发送模块统一处理所有发往企业微信的消息。

负责：

- 创建 outbox 发送任务。
- 普通回复。
- 流式回复。
- 主动发送消息。
- 主动 @ 用户。
- 发送状态更新。
- 失败重试。
- 频率控制。

不负责：

- 判断是否需要发送。
- 生成 AI 回复内容。
- 修改 Dify 工作流。

## 2. 发送场景

| scene | 说明 |
| --- | --- |
| reply | 用户触发后的机器人回复 |
| proactive | 定时意图识别后的主动提醒 |
| summary | 会话摘要通知 |
| alert | 系统告警 |

## 3. 处理流程

### 3.1 普通发送

1. 创建 `outbox_messages`。
2. 状态为 `pending`。
3. Dispatcher 获取待发送任务。
4. 调用企微发送适配器。
5. 成功后状态改为 `sent`。
6. 失败后记录错误并按策略重试。

当前自动客服实机阶段允许补漏 worker 在 `WECOM_MESSAGE_RECONCILE_AUTO_SEND=true` 时创建 proactive outbox。`aibot_ws` 模式下由长连接 worker 复用回复机器人 WebSocket 连接派发并回写 `sent/failed`；非 `aibot_ws` sender 仍可直发。独立 Dispatcher 仍作为后续生产化重试、限流和恢复能力。

### 3.2 流式回复

1. @ 消息触发 Dify streaming。
2. Dify Gateway 接收增量内容。
3. Outbound Dispatcher 通过企微流式能力发送增量。
4. 最终帧发送 `finish=true`。
5. 保存完整回复到 `bot_replies`。

## 4. outbox 内容结构

示例：

```json
{
  "scene": "proactive",
  "chatid": "CHATID",
  "target_userids": ["lisi"],
  "msgtype": "markdown",
  "content": {
    "markdown": {
      "content": "<@lisi> 请确认一下报价方案。"
    }
  },
  "source_type": "ai_run",
  "source_id": "airun_20260504_000002"
}
```

## 5. 幂等策略

发送任务必须有 `outbox_id` 和业务幂等键。

建议：

- 实时回复：`reply_{source_message_id}_{workflow_code}`
- 主动提醒：`intent_{evidence_msgid}_{target_userid}`
- 会话摘要：`summary_{conversation_no}`

## 6. MVP 实现范围

必须实现：

- 创建 outbox 任务。
- 普通 markdown/text 发送。
- 发送状态记录。
- 失败错误记录。

优先验证：

- 流式回复。
- @ 指定用户。

可延后：

- 模板卡片。
- 附件发送。
- 复杂频控。

## 7. 验收标准

- 所有发送都能在 `outbox_messages` 查到。
- 发送成功能保存状态和企微回执。
- 发送失败能保存错误原因。
- 同一业务幂等键不会重复发送。
