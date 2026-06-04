# 06. 会话智能模块

本文档定义 `v0.3` 会话切分与会话沉淀模块的新版 Interface。旧 `conversation_segmentation` 工作流说明已经移除，后续不要按旧四工作流口径恢复 Dify 应用。

## 1. 模块目标

会话智能模块的第一版目标是：

```text
单日全量群消息 + 群用户信息
-> Dify 判断会话切分位置
-> 自建系统校验切分位置
-> 自建系统按切分位置生成 conversation_segments 并入库
```

这里 Dify 不直接输出最终会话段，也不生成 `conversation_no`。Dify 只输出“应该在哪里切开”的位置数组；最终切分、编号、版本、写库都由自建系统完成。

## 2. 执行频度

第一版采用 **每天一次 T+1 批处理**。

推荐调度：

```text
每天 02:00 执行
扫描上一自然日 00:00:00 到 23:59:59 的消息
按 chatid 分组执行会话切分
```

保留两个入口：

- 定时入口：每天自动处理上一自然日。
- 手动入口：指定 `chatid + date` 或 `chatid + start_time + end_time` 重跑，方便补数据和调试。

约束：

- 会话切分不参与实时 @ 回复。
- 会话切分不负责发送企微消息。
- 单日全量输入必须包含用户消息和机器人回复消息。
- 自建系统负责防止同一天同一群重复写入同一批会话段。

## 3. 现有接口盘点

当前代码中已有历史入口：

```http
POST /api/conversations/segment/run
```

当前请求体：

```json
{
  "chatid": "CHAT_A",
  "start_time": "2026-06-03T00:00:00+08:00",
  "end_time": "2026-06-03T23:59:59+08:00",
  "mode": "auto"
}
```

V0.3 可以继续保留该 API 路径，但内部语义需要调整为“运行会话切分任务”，不是旧的直接写 Dify segments。

当前查询入口继续保留：

```http
GET /api/conversations?chatid=CHAT_A
GET /api/conversations/{conversation_no}
```

V0.3 实际消息库全量测试新增入口：

```http
POST /api/ai-memory/full-test/run
```

该入口按北京时间自然日直接读取 `messages` 表，适合在 Dify DSL 导入完成后做实库回归：

```json
{
  "target_date": "2026-06-04",
  "chatids": ["CHAT_A"],
  "run_profiles": true
}
```

- `chatids` 为空时处理当天有消息的全部群。
- `run_profiles=true` 时，会话切分成功后继续调用 `user_profile_update`。
- 常驻定时 worker 使用同一套服务逻辑，避免手动入口和定时入口行为分叉。
- 如果同一天同一群已经存在 active `conversation_segments`，系统会复用既有会话段并跳过 Dify 切分，避免重复定时任务写出重叠会话。

## 4. 系统侧请求结构

建议 V0.3 请求结构：

```json
{
  "chatid": "CHAT_A",
  "date": "2026-06-03",
  "start_time": "2026-06-03T00:00:00+08:00",
  "end_time": "2026-06-03T23:59:59+08:00",
  "mode": "daily",
  "options": {
    "include_bot_messages": true,
    "include_user_directory": true,
    "max_messages": 1000,
    "dify_repair_attempts": 2,
    "system_retry_attempts": 1
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `chatid` | string | 是 | 群聊 ID。 |
| `date` | string | 否 | 单日任务日期，格式 `YYYY-MM-DD`。 |
| `start_time` | string | 是 | 查询窗口开始时间。 |
| `end_time` | string | 是 | 查询窗口结束时间。 |
| `mode` | string | 否 | `daily` / `manual` / `replay`。 |
| `options.include_bot_messages` | boolean | 是 | 第一版必须为 true，机器人回复参与上下文。 |
| `options.include_user_directory` | boolean | 否 | 是否带入群用户信息。 |
| `options.max_messages` | integer | 否 | 单次输入消息上限；超过时系统需要分块或记录失败。 |
| `options.dify_repair_attempts` | integer | 否 | Dify 内部输出修复次数，第一版固定为 2。 |
| `options.system_retry_attempts` | integer | 否 | 自建系统重试 Dify run 次数，第一版固定为 1。 |

## 5. Dify 输入结构

新版 Dify 应用建议命名：

```text
conversation_boundary_detection
```

Dify Start 节点只声明：

```json
{
  "variable": "payload",
  "type": "json_object",
  "required": true
}
```

系统调用 Dify 时传：

```json
{
  "payload": {
    "task": "conversation_boundary_detection",
    "chat": {
      "chatid": "CHAT_A",
      "chattype": "group",
      "name": "测试群"
    },
    "window": {
      "date": "2026-06-03",
      "start_time": "2026-06-03T00:00:00+08:00",
      "end_time": "2026-06-03T23:59:59+08:00",
      "timezone": "Asia/Shanghai"
    },
    "messages": [
      {
        "index": 0,
        "message_id": "123",
        "msgid": "MSG_001",
        "business_identity_key": "biz_xxx",
        "canonical_message_id": "123",
        "chatid": "CHAT_A",
        "userid": "USER_A",
        "sender_type": "user",
        "bot_role": null,
        "msgtype": "text",
        "content": "这个报价方案什么时候能确认？",
        "quote_msgid": null,
        "mentioned_bot": false,
        "mentioned_users": [],
        "create_time": "2026-06-03T10:01:00+08:00"
      }
    ],
    "users": [
      {
        "userid": "USER_A",
        "name": "张三",
        "alias": null,
        "department": null,
        "status": "active"
      }
    ],
    "existing_segments": [],
    "runtime": {
      "source": "daily_job",
      "workflow_code": "conversation_boundary_detection",
      "response_mode": "blocking",
      "language": "zh-CN"
    }
  }
}
```

约束：

- `messages` 必须按 `create_time asc, id asc` 排序。
- `messages` 必须包含用户消息和机器人回复。
- `messages[].index` 由系统生成，必须从 0 连续递增。
- `messages[].msgid` 必须来自 `messages.external_msgid`。
- `users` 只传系统已知用户，Dify 不补全用户身份。
- Dify 不允许编造 `msgid/userid`。

## 6. Dify 输出结构

Dify End 节点必须输出合法 JSON。

成功输出：

```json
{
  "status": "success",
  "split_positions": [
    {
      "after_msgid": "MSG_010",
      "before_msgid": "MSG_011",
      "reason_type": "time_gap",
      "reason": "两条消息间隔超过 45 分钟，且后续话题发生变化。",
      "confidence": 0.86
    }
  ],
  "confidence": 0.84,
  "error": null
}
```

无须切分时：

```json
{
  "status": "success",
  "split_positions": [],
  "confidence": 0.75,
  "error": null
}
```

Dify 内部重试仍失败时，必须返回合法失败 JSON：

```json
{
  "status": "failed",
  "split_positions": [],
  "confidence": 0,
  "error": {
    "reason": "LLM 输出在 3 次尝试后仍不符合 JSON schema。",
    "last_error": "split_positions[0].after_msgid not found in input messages",
    "attempts": 3
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `status` | string | 是 | `success` / `failed`。 |
| `split_positions` | array | 是 | 切分位置数组。 |
| `split_positions[].after_msgid` | string | 是 | 切分点前一条消息，必须来自输入。 |
| `split_positions[].before_msgid` | string | 是 | 切分点后一条消息，必须来自输入。 |
| `split_positions[].reason_type` | string | 是 | `time_gap` / `intent_shift` / `topic_shift` / `task_closed` / `manual_hint` / `other`。 |
| `split_positions[].reason` | string | 是 | 切分原因。 |
| `split_positions[].confidence` | number | 是 | 单个切分点置信度。 |
| `confidence` | number | 是 | 整体置信度。 |
| `error` | object/null | 是 | 成功时为 null，失败时记录原因。 |

## 7. 切分规则提示词口径

LLM Prompt 必须包含以下规则：

- 时间切分：消息间隔显著变长时倾向切分，但不能只凭时间切分。
- 意图切分：从咨询、确认、安排、闲聊等意图明显变化时可以切分。
- 主题切分：业务对象、客户、问题域、任务目标变化时可以切分。
- 任务闭环：一个问题已经得到明确回答、确认或后续动作后，后续新问题可以切分。
- 引用连续性：如果 `quote_msgid` 指向前文，优先保持同一会话。
- 机器人回复连续性：机器人回复通常跟随前一条用户问题，不应单独形成新会话。
- 噪声处理：表情、确认收到、短回复不应单独触发切分。
- 最小粒度：不要把每条消息都切成独立会话。
- 最大粒度：不要把明显跨多个主题的一整天消息合并成一个会话。
- 证据约束：只能使用输入中的 `msgid/userid`。

## 8. Dify 节点编排

第一版建议采用显式两次修复链路，避免依赖复杂 Loop 节点。

```text
Start(payload)
-> Code 输入提取与基础校验
-> If/Else 空消息或消息不足
-> Template Transform 首次 Prompt
-> LLM 首次切分
-> Code JSON 解析与规范校验
-> If/Else 首次输出有效？
   -> 有效：End
   -> 无效：Template Transform 修复 Prompt 1，带上错误原因和上次输出
        -> LLM 修复 1
        -> Code 校验 1
        -> If/Else 修复 1 有效？
             -> 有效：End
             -> 无效：Template Transform 修复 Prompt 2，带上错误原因和上次输出
                  -> LLM 修复 2
                  -> Code 校验 2
                  -> Code 生成最终 success/failed 标准 JSON
                  -> End
```

Dify 内部最多执行：

```text
首次 LLM + 2 次修复 LLM = 3 次 LLM 尝试
```

每次修复 Prompt 必须包含：

- 原始输出 schema。
- 上一次 LLM 原始输出。
- 上一次校验错误原因。
- 明确要求只返回 JSON，不输出解释性自然语言。

## 9. 自建系统校验与重试

系统收到 Dify 输出后必须校验：

- 顶层 JSON 合法。
- `status` 只能是 `success/failed`。
- `split_positions` 为数组。
- 每个 `after_msgid` 和 `before_msgid` 必须存在于输入消息。
- `after_msgid` 和 `before_msgid` 必须相邻，且 `after` 在 `before` 前。
- 切分点不能重复。
- 切分点必须按消息顺序递增。

重试策略：

```text
Dify 内部重试 2 次。
如果 Dify 返回 status=failed 或系统校验失败：
  自建系统最多重新发起 1 次新的 Dify run。
如果第二个 Dify run 仍失败：
  标记 conversation_boundary_job=failed
  写入 ai_runs.error_message / job.last_error
  不写 conversation_segments
```

注意：

- 自建系统重试是重新调用整个 Dify workflow，不是让同一个 Dify run 继续。
- 彻底失败必须有日志能定位 `chatid/date/run_id/error`。
- 失败不能阻塞其他群的日切分任务。

## 10. 入库映射

自建系统按 `split_positions` 生成会话段：

```text
day_messages[0..split_1.after]
split_1.before..split_2.after
split_2.before..end
```

写入 `conversation_segments`：

| 字段 | 来源 |
| --- | --- |
| `conversation_no` | 系统生成 |
| `chatid` | 请求 chatid |
| `start_message_id` | 每段第一条 message.id |
| `end_message_id` | 每段最后一条 message.id |
| `start_time` | 每段第一条 message.create_time |
| `end_time` | 每段最后一条 message.create_time |
| `title` | 第一版可由系统生成占位标题，后续可加摘要工作流 |
| `summary` | 第一版可为空摘要或系统占位，后续可加摘要工作流 |
| `keywords` | 第一版可为空数组 |
| `participants` | 系统从该段用户消息中提取 |
| `ai_run_id` | Dify run id |
| `confidence` | 对应切分置信度或整体置信度 |
| `version` | 系统生成 |
| `status` | `active` |

## 11. 当前代码差距

- 当前 `run_conversation_segmentation()` 仍按旧 `conversation_segmentation` workflow 读取和写入。
- 当前 Dify 输出是 `segments`，需要改成 `split_positions`。
- 当前缺少日切分 job 状态表或等效状态记录。
- 当前缺少 Dify 内部两次修复链路对应 DSL。
- 当前缺少系统侧失败后最多重试一次的状态控制。

## 12. 验收

- 单日全量消息包含用户消息和机器人回复。
- Dify 成功输出切分点后，系统能正确切成多个 `conversation_segments`。
- Dify 编造 `msgid` 时会在 Dify 内修复；修复失败后返回 `status=failed`。
- Dify 失败后系统最多重试一次。
- 两次 Dify run 都失败时不写入会话段，并记录清晰日志。
