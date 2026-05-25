# 03. 触发路由模块

## 1. 模块职责

触发路由模块负责判断一条消息或一批消息是否需要进入 AI 工作流。

负责：

- @ 机器人事件触发。
- 关键词定时扫描线索。
- 定时扫描触发。
- 触发事件记录。
- 选择 Dify 工作流。
- 创建 AI 调用任务。

不负责：

- 实际调用 Dify。
- 发送企微消息。
- 写入会话切分或用户画像结果。

## 2. 触发类型

| 类型 | trigger_type | 说明 |
| --- | --- | --- |
| @ 机器人 | mention | 企微 @ 事件回调或长连接实时消息触发 |
| 关键词 | keyword | 系统调度器扫描已入库消息窗口后形成意图识别线索 |
| 定时意图识别 | schedule | 调度器扫描消息窗口并调用 `intent_detection` |
| 手动触发 | manual | 管理后台或开发调试触发 |

## 3. 规则配置

规则保存在 `trigger_rules`。

关键字段：

- `rule_code`
- `trigger_type`
- `config`
- `workflow_code`
- `priority`
- `enabled`

关键词规则配置示例：

```json
{
  "keywords": ["报价", "合同", "跟进"],
  "match_mode": "contains",
  "case_sensitive": false
}
```

## 4. 处理流程

### 4.1 @ 事件触发

1. 企微通过 URL 回调或长连接推送 @ 机器人事件或实时消息。
2. Message Ingestion 将消息标准化，并标记 `mentioned_bot=true`。
3. Trigger Router 接收 `message_id`。
4. 读取标准化消息并确认 `mentioned_bot=true`。
5. 创建 `mention` 类型 `trigger_events`。
6. 调用 Dify Gateway 创建 `reply_generation` 的 `ai_runs`。

### 4.2 关键词定时扫描触发

1. 调度器选择 chatid 和时间窗口。
2. 查询窗口内已入库消息。
3. 按 `keyword` 规则配置匹配消息内容。
4. 对命中的消息或窗口创建 `keyword` 类型 `trigger_events`。
5. 将命中关键词、消息窗口和已知用户作为 `intent_detection` 输入。
6. 调用 Dify Gateway 创建 `intent_detection` 的 `ai_runs`。

### 4.3 定时意图识别触发

1. 调度器选择 chatid 和时间窗口。
2. 查询窗口内消息。
3. 创建 `schedule` 类型触发事件或直接创建定时意图识别运行记录。
4. 调用 Dify Gateway 创建 `intent_detection` 的 `ai_runs`。

## 5. 幂等策略

同一消息对同一规则只能触发一次。

唯一键建议：

- `rule_code + message_id`
- 关键词窗口扫描可使用 `rule_code + chatid + start_time + end_time + first_matched_msgid`

定时扫描建议：

- `rule_code + chatid + start_time + end_time`

## 6. MVP 实现范围

必须实现：

- mention 触发。
- schedule 触发。
- `trigger_events` 记录。
- 根据 `workflow_code` 创建 AI 调用。

可延后：

- 关键词定时扫描执行器。
- 复杂关键词规则。
- 正则匹配。
- 多规则优先级冲突处理。

## 7. 验收标准

- @ 消息能创建 `reply_generation` 触发事件。
- 同一条 @ 消息不会重复触发。
- 关键词扫描只基于已入库消息窗口创建 `keyword` 触发事件，不依赖企微实时关键词回调。
- 定时扫描能创建 `intent_detection` AI 调用。
- 触发记录能追踪到消息和工作流。
