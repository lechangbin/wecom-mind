# 07. 用户画像模块

本文档定义 `v0.3` 用户画像自动更新模块的新版 Interface。旧 `user_profile_analysis` 工作流说明已经移除，后续不要按旧四工作流口径恢复 Dify 应用。

## 1. 模块目标

用户画像模块的第一版目标是：

```text
一个 conversation 的全部消息
+ 一个 target_userid
+ 该用户当前画像
-> Dify 输出该用户的更新画像
-> 自建系统校验并写入新画像版本
```

一个会话里可能有多个用户。系统侧应为每个需要更新的用户创建独立画像更新请求。

## 2. 并行与状态机原则

画像更新请求需要异步并行，但必须避免之前回复链路中出现过的状态串线问题。

第一版规则：

- 不同 `userid` 的画像更新可以并行。
- 同一 `userid` 的画像更新默认串行，防止多个任务基于同一份旧画像同时写入。
- 每个画像更新请求必须有独立状态记录。
- 每个请求必须绑定 `conversation_no + userid + source_profile_version`。
- Dify blocking 调用前必须先写入 `running` 状态并提交事务。
- Dify 返回后必须校验 `userid`、证据和版本，再写入画像。
- 如果写入前发现用户画像版本已变化，必须标记 `version_conflict`，重新排队或重新读取画像后再执行。

建议新增状态 Module：

```text
profile_update_requests
```

核心字段：

| 字段 | 说明 |
| --- | --- |
| `request_id` | 画像更新请求 ID |
| `conversation_no` | 来源会话 |
| `userid` | 目标用户 |
| `source_profile_version` | 调用 Dify 前读取到的画像版本 |
| `target_profile_version` | 计划写入的新画像版本 |
| `status` | 生命周期状态 |
| `ai_run_id` | 关联 Dify run |
| `claimed_by` | worker 标识 |
| `claimed_at` | 认领时间 |
| `started_at` | Dify 开始时间 |
| `completed_at` | 完成时间 |
| `last_error` | 最后错误 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

状态：

```text
pending
claimed
running
succeeded
writing
completed
no_change
failed
version_conflict
stalled
canceled
```

唯一约束建议：

```text
conversation_no + userid + source_profile_version
```

同一用户并发约束：

```text
同一 userid 同一时间最多一个 running/writing 请求。
```

这能保证“异步并行”发生在不同用户之间，而不是让同一个用户的画像版本互相覆盖。

## 3. 触发来源

画像更新由会话切分完成后触发：

```text
conversation_segments 写入成功
-> 系统提取 participants
-> 为每个 userid 创建 profile_update_request
-> worker 异步并行处理不同 userid
```

第一版可以先支持手动触发：

```http
POST /api/profiles/analyze/run
```

但内部语义需要从“按用户时间窗口分析”调整为“按会话和用户更新画像”。

建议请求体：

```json
{
  "conversation_no": "conv_20260603_abc123",
  "userid": "USER_A",
  "mode": "incremental",
  "options": {
    "allow_version_conflict_requeue": true,
    "min_fact_confidence_to_store": 0.5,
    "min_fact_confidence_to_activate": 0.75
  }
}
```

## 4. Dify 输入结构

新版 Dify 应用建议命名：

```text
user_profile_update
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
    "task": "user_profile_update",
    "userid": "USER_A",
    "conversation": {
      "conversation_no": "conv_20260603_abc123",
      "chatid": "CHAT_A",
      "start_time": "2026-06-03T10:00:00+08:00",
      "end_time": "2026-06-03T10:08:00+08:00",
      "participants": ["USER_A", "USER_B"]
    },
    "messages": [
      {
        "message_id": "123",
        "msgid": "MSG_001",
        "chatid": "CHAT_A",
        "userid": "USER_A",
        "sender_type": "user",
        "bot_role": null,
        "msgtype": "text",
        "content": "我想确认一下报价和交付周期。",
        "quote_msgid": null,
        "mentioned_bot": false,
        "create_time": "2026-06-03T10:01:00+08:00"
      },
      {
        "message_id": "124",
        "msgid": "MSG_002",
        "chatid": "CHAT_A",
        "userid": "aib5xxx",
        "sender_type": "bot",
        "bot_role": "reply_bot",
        "msgtype": "text",
        "content": "正在查询相关资料。",
        "quote_msgid": "MSG_001",
        "mentioned_bot": false,
        "create_time": "2026-06-03T10:01:05+08:00"
      }
    ],
    "target_user_messages": [
      {
        "message_id": "123",
        "msgid": "MSG_001",
        "content": "我想确认一下报价和交付周期。",
        "create_time": "2026-06-03T10:01:00+08:00"
      }
    ],
    "current_profile": {
      "userid": "USER_A",
      "summary": "该用户关注报价。",
      "version": 1,
      "confidence": 0.76,
      "facts": [
        {
          "fact_id": "fact_1001",
          "fact_type": "interest",
          "label": "关注报价策略",
          "description": "经常询问报价和折扣。",
          "evidence_msgids": ["MSG_OLD_001"],
          "evidence_conversation_nos": [],
          "confidence": 0.8,
          "status": "active"
        }
      ]
    },
    "runtime": {
      "source": "profile_update_request",
      "workflow_code": "user_profile_update",
      "response_mode": "blocking",
      "mode": "incremental",
      "language": "zh-CN"
    }
  }
}
```

约束：

- `payload.userid` 是唯一目标用户。
- `payload.conversation.conversation_no` 是唯一来源会话编号，必须存在。
- 不要把 `conversation_no` 放在 payload 顶层；旧的顶层 `payload.conversation_no` 不再是正式契约。
- `messages` 是本会话全部消息，包含机器人回复。
- `target_user_messages` 是目标用户自己的消息。
- 画像事实证据优先来自 `target_user_messages`。
- 如果事实只能从他人消息或机器人回复中推断，必须标记较低置信度或 `source=context_inferred`。
- Dify 不允许编造 `userid/msgid/conversation_no/fact_id`。
- 如果 `payload.conversation.conversation_no` 缺失，Dify 应返回 `profile_action=no_change` 兜底，系统侧记录日志用于排查。

实库全量测试入口：

- `POST /api/ai-memory/full-test/run` 在 `run_profiles=true` 时会先写入 `conversation_segments`，再按每个会话内的用户调用 `user_profile_update`。
- `scripts/run_ai_memory_full_test_worker.py` 使用同一逻辑；一键启动脚本在 `AI_MEMORY_FULL_TEST_ENABLED=true` 时会启动该 worker。
- 本地测试时建议先手动调用一次 API，确认 `profile_results[]` 后，再打开定时 worker。
- 如果同一 `conversation_no + userid` 已经有画像事实证据落库，全量测试入口会返回 `profile_action=already_updated` 并跳过，避免定时任务重复写画像版本。

## 5. Dify 输出结构

第一版输出应是“更新后的完整画像 + 变更说明”。

兜底输出：

```json
{
  "userid": "USER_A",
  "profile_action": "no_change",
  "updated_profile": {
    "summary": "",
    "facts": []
  },
  "changes": {
    "facts_added": [],
    "facts_updated": [],
    "facts_retired": []
  },
  "evidence": {
    "conversation_no": "conv_20260603_abc123",
    "msgids": []
  },
  "confidence": 0
}
```

更新输出：

```json
{
  "userid": "USER_A",
  "profile_action": "update",
  "updated_profile": {
    "summary": "该用户近期关注报价确认、交付周期和客户跟进。",
    "facts": [
      {
        "fact_id": "fact_1001",
        "fact_type": "interest",
        "label": "关注报价与交付策略",
        "description": "用户不仅关注报价，也关注报价对应的交付周期。",
        "evidence_msgids": ["MSG_001"],
        "evidence_conversation_nos": ["conv_20260603_abc123"],
        "source": "direct",
        "confidence": 0.84,
        "status": "active"
      }
    ]
  },
  "changes": {
    "facts_added": [
      {
        "fact_type": "business_need",
        "label": "关注交付周期",
        "description": "用户明确询问报价对应的交付周期。",
        "evidence_msgids": ["MSG_001"],
        "evidence_conversation_nos": ["conv_20260603_abc123"],
        "source": "direct",
        "confidence": 0.82
      }
    ],
    "facts_updated": [
      {
        "fact_id": "fact_1001",
        "reason": "新增交付周期关注点。",
        "evidence_msgids": ["MSG_001"],
        "confidence": 0.84
      }
    ],
    "facts_retired": []
  },
  "evidence": {
    "conversation_no": "conv_20260603_abc123",
    "msgids": ["MSG_001"]
  },
  "confidence": 0.82
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `userid` | string | 是 | 必须等于输入 `payload.userid`。 |
| `profile_action` | string | 是 | `create` / `update` / `no_change`。 |
| `updated_profile` | object | 是 | 更新后的完整画像快照。 |
| `updated_profile.summary` | string | 是 | 新画像摘要。 |
| `updated_profile.facts` | array | 是 | 更新后的事实列表。 |
| `changes` | object | 是 | 本次变更说明。 |
| `evidence.conversation_no` | string | 是 | 来源会话。 |
| `evidence.msgids` | array | 是 | 总证据消息。 |
| `confidence` | number | 是 | 本次画像输出置信度。 |

## 6. 系统校验规则

系统必须校验：

- `output.userid == payload.userid`。
- `evidence.conversation_no == payload.conversation.conversation_no`。
- 所有 `evidence_msgids` 必须来自输入 `messages`。
- `source=direct` 的事实证据必须至少包含一条目标用户自己的消息。
- `changes.facts_updated[].fact_id` 必须来自 `current_profile.facts`。
- `updated_profile.facts[].fact_id` 如果引用旧事实，必须来自 `current_profile.facts`。
- 不能写入低于系统阈值的事实。
- 写入前必须再次读取当前用户画像版本。
- 如果当前版本不等于 `source_profile_version`，不得直接写入，必须进入 `version_conflict`。

## 7. Dify 节点编排

第一版建议：

```text
Start(payload)
-> Code 输入提取与基础校验
-> If/Else userid 或 conversation 缺失？
-> Template Transform 画像更新 Prompt
-> LLM 生成更新画像
-> Code JSON 解析/校验/兜底
-> If/Else 输出有效？
   -> 有效：End
   -> 无效：Template Transform 修复 Prompt 1
        -> LLM 修复 1
        -> Code 校验 1
        -> If/Else 修复 1 有效？
             -> 有效：End
             -> 无效：Template Transform 修复 Prompt 2
                  -> LLM 修复 2
                  -> Code 校验 2
                  -> Code 生成标准 no_change 或 failed 结构
                  -> End
```

Dify 内部同样最多：

```text
首次 LLM + 2 次修复 LLM = 3 次 LLM 尝试
```

## 8. 写库策略

写入 `user_profiles`：

- 每次成功更新生成新 version。
- 旧 active profile 标记为 `superseded`。
- `updated_profile` 写入 `profile_json`。
- `summary/confidence/ai_run_id/version/status` 独立写入。

写入 `user_profile_facts`：

- `confidence >= 0.75` 写入 `active`。
- `0.5 <= confidence < 0.75` 写入 `low_confidence`。
- `confidence < 0.5` 不写 fact，只保留在 `ai_runs.output_json`。
- `facts_retired` 对应旧 fact 标记为 `expired`。

## 9. 当前代码差距

- 当前 `run_user_profile_analysis()` 仍按 `userid + time_range` 查询。
- 当前缺少 `profile_update_requests` 状态机。
- 当前未处理同一用户画像更新并发版本冲突。
- 当前 Dify 输入不是 `payload` 包裹结构。
- 当前输出是 `facts_to_add/update/retire`，需要调整为完整 `updated_profile + changes`。
- 当前 @ 回复和主动回复工作流尚未消费用户画像。

## 10. 验收

- 一个会话多个用户时，系统能创建多个画像更新请求。
- 不同用户画像更新可以并行。
- 同一用户画像更新不会并发覆盖。
- Dify 输出编造 `userid/msgid/fact_id` 时不写库。
- 版本冲突时不会覆盖新画像。
- 画像更新后，回复工作流可读取并传入该用户画像。
