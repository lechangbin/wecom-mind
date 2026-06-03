# 02. 消息处理模块

## 1. 模块职责

消息处理模块负责把企微原始消息沉淀成系统可查询、可分析、可触发的标准化消息。

负责：

- 保存原始消息。
- 标准化消息字段。
- 幂等排重。
- 识别消息类型。
- 提取文本内容。
- 保存 quote 引用内容。
- 识别是否 @ 机器人。
- 更新群聊和用户活跃时间。

不负责：

- 调用 Dify。
- 决定是否回复。
- 发送消息。
- 生成会话摘要或画像。

## 2. 输入输出

输入：

```json
{
  "source": "mcp",
  "idempotency_key": "mcp_msg_xxx",
  "raw_message": {}
}
```

输出：

```json
{
  "raw_message_id": 20001,
  "message_id": 30001,
  "duplicated": false
}
```

## 3. 标准化字段

标准消息至少包含：

- `external_msgid`
- `chatid`
- `chattype`
- `userid`
- `msgtype`
- `content_text`
- `normalized_content`
- `quote_message`
- `quote_msgid`
- `mentioned_bot`
- `mentioned_users`
- `create_time`

## 4. 幂等策略

优先使用：

- 企微 `msgid`
- `source + external_msgid`
- 回调来源没有 msgid 时使用 payload hash

同一幂等键重复入库时：

- 不重复插入 `messages_raw`。
- 不重复插入 `messages`。
- 返回 `duplicated=true`。

跨来源同一业务消息：

- 不再直接吞掉另一来源记录。
- 使用 `business_identity_key = chatid + userid + canonical_content + 5 秒时间桶` 关联。
- 后续触发链路以 `mention_requests.business_identity_key` 防止重复 Dify 和重复回复。

## 5. 处理流程

1. 接收原始消息。
2. 生成幂等键。
3. 检查是否已存在。
4. 写入 `messages_raw`。
5. 解析消息类型。
6. 提取文本和引用内容。
7. 写入 `messages`。
8. 生成 `business_identity_key` 并写入 `canonical_message_id`。
9. 更新 `wecom_chats.last_message_at`。
10. 更新 `wecom_users.last_active_at`。
11. 触发 Trigger Router。

## 6. MVP 实现范围

必须支持：

- 文本消息。
- quote 字段保存。
- @ 机器人识别。
- 消息排重。

可延后：

- 图片下载。
- 文件下载。
- 语音内容解析。
- mixed 图文混排深度解析。

## 7. 验收标准

- 同一消息重复处理只生成一条标准消息。
- 文本消息能正确提取 `content_text`。
- @ 机器人消息能标记 `mentioned_bot=true`。
- quote 内容能完整保存。
- 消息能按 `chatid + create_time` 查询。
