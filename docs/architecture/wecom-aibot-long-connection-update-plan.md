# 企业微信智能机器人长连接后续更新计划

## 定位

本计划属于 `v0.1-dify-ai-baseline` 之后的后续更新，不混入已完成的 Dify 双应用基线版本。

已完成基线负责：

- Dify 双应用：`group_knowledge_reply`、`chat_proactive_reminder`。
- 本地消息表、触发规则、AI 调用审计、outbox 审计。
- Dify blocking API 集成、输出结构校验、outbox 发送抽象。
- 架构展示页与 DSL 初版。

本后续更新负责：

- 用企业微信智能机器人长连接模式替代旧 MCP/HTTP 回调主入口。
- 建立实时消息接收、消息入库、触发、Dify、发送的实机闭环。
- 将历史消息拉取作为补漏能力，而不是主接收入口。

## Git 版本边界

| 版本/分支 | 用途 |
| --- | --- |
| `main` | 指向已完成的 Dify AI baseline 发布点。 |
| `v0.1-dify-ai-baseline` | 已完成内容的发布标签。 |
| `codex/wecom-aibot-long-connection` | 后续长连接更新分支。 |

## 架构决策

1. 不再使用 MCP HTTP callback 作为主入口。
2. 企业微信实时消息接收改为智能机器人长连接 Worker。
3. Bot ID 和 Secret 只允许放在本地 `.env` 或部署环境变量，不写入文档、测试、提交记录。
4. 历史消息读取使用 `get_msg_chat_list` / `get_message`，仅用于补漏、初始化或人工排查。
5. 发送优先走智能机器人长连接能力，保留 outbox 审计和幂等。
6. Dify 仍只负责 AI 判断与生成，不负责企微发送、数据库写入、幂等、重试。

## 目标闭环

```text
AiBot 长连接收到消息
-> 标准化为 MessageIngestRequest
-> ingest_message 写 messages_raw / messages
-> @ 消息自动 evaluate_triggers
-> group_knowledge_reply
-> create reply outbox
-> 长连接发送
-> outbox 标记 sent / failed
```

非 @ 消息闭环：

```text
AiBot 长连接收到非 @ 群消息
-> 标准化入库
-> 定时窗口扫描本地 messages
-> chat_proactive_reminder
-> create proactive outbox
-> 长连接发送
```

## 阶段拆分

### 阶段 1：长连接接收适配层

目标：

- 新增 `WeComAiBotLongConnectionWorker`。
- 新增 `AiBotFrameNormalizer`。
- 将长连接收到的消息转换为现有 `MessageIngestRequest`。
- 复用 `ingest_message()`，不重写消息入库逻辑。

验收：

- mock frame 可以入库为 `messages_raw` 和 `messages`。
- text / mixed 文本可正确提取。
- `userid`、`chatid`、`msgid` 不编造。
- `mentioned_bot` 仍由现有规则计算。

### 阶段 2：自动触发编排

目标：

- 新增消息入库后的编排服务，例如 `process_incoming_message()`。
- @ 消息自动执行 `evaluate_triggers`。
- 成功生成 reply outbox。
- 非 @ 消息只入库，不立即 Dify 回复。

验收：

- 单条 @ 测试消息可走到 `group_knowledge_reply`。
- Dify 输出不合法时不会发送。
- 重复 msgid 不重复触发。

### 阶段 3：长连接发送适配层

目标：

- 新增 `WECOM_SENDER_MODE=aibot_ws`。
- 新增 `WeComAiBotWsMessageSender`。
- outbox 继续作为唯一发送入口。
- 支持 text / markdown 的基础发送。

验收：

- pending outbox 可以通过长连接发送。
- 发送成功写入 `external_msgid` 和 `raw_response`。
- 发送失败写入 `failed`、`error_code`、`error_message`。

### 阶段 4：常驻运行与恢复

目标：

- 增加 worker 启动入口。
- 增加断线重连、退避、运行日志。
- 支持本地开发手动启动，生产环境可由进程管理器托管。

验收：

- 断线后可以重连。
- worker 启动失败不会影响 FastAPI 普通接口启动。
- 日志不打印 Secret、access token 或完整认证响应。

### 阶段 5：历史消息补漏

目标：

- 复用测试目录中已验证的 MCP 消息读取思路。
- 实现 `get_msg_chat_list` / `get_message` 的 Python 适配。
- 将补漏拉取结果走同一个 `ingest_message()`。

验收：

- 指定 `chatid + time_range` 可补拉最近 7 天消息。
- `next_cursor` 可分页。
- 重复消息不重复入库。

### 阶段 6：旧入口收敛

目标：

- 将 `/api/wecom/callbacks/mcp` 从主文档中移除。
- 旧 callback 代码保留为兼容或移除，视实测稳定性决定。
- HTML 架构图更新为长连接主链路。

验收：

- 文档中不再把 MCP callback 写作主接收链路。
- `message-execution-flow.html` 展示长连接 Worker。
- 旧入口不会误导后续 agent。

## 并行开发建议

可以并行：

- `AiBotFrameNormalizer` 单元测试与实现。
- `WeComAiBotWsMessageSender` mock 测试与实现。
- HTML / 文档更新。
- 历史消息补漏 adapter 的接口设计。

需要串行：

1. 先确定长连接 SDK 的真实 frame 格式。
2. 再实现 normalizer。
3. 再接入 `process_incoming_message()`。
4. 最后做真实企微群实机闭环。

## 风险与优先级

| 优先级 | 风险 | 处理 |
| --- | --- | --- |
| P0 | 长连接 SDK frame 格式与当前假设不一致 | 先用真实群收一条消息，保存脱敏样例。 |
| P0 | Secret 泄露 | 只写 `.env`，提交前做密钥扫描。 |
| P1 | 长连接发送和现有 app/webhook sender 语义不同 | 保留 `WeComMessageSender` 接口，新增 sender 模式。 |
| P1 | Worker 和 FastAPI 生命周期耦合过重 | worker 独立启动，FastAPI 不默认启动长连接。 |
| P2 | 历史补漏与实时消息重复 | 统一使用 msgid 幂等。 |

## 第一批实机测试脚本

1. 启动 API 服务。
2. 启动长连接 worker。
3. 在测试群发送非 @ 消息，确认只入库不回复。
4. 在测试群发送 @ 机器人问题，确认入库、触发 Dify、生成 outbox、发送回复。
5. 重复发送同一测试 payload，确认不重复入库和不重复发送。
6. 断开网络或停止 worker，确认重连和错误日志可读。

