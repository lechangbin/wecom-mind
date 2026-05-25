# 07. Dify 功能阶段推进预排

本文档用于把当前 Dify 架构文档推进成可执行实施阶段。当前只做阶段预排，不启动子代理、不修改 Dify 实例、不改业务代码。

后续执行时必须以本目录文档为准：

- [README.md](./README.md)
- [00-api-blocking-node-skeleton.md](./00-api-blocking-node-skeleton.md)
- [01-intent_detection.md](./01-intent_detection.md)
- [02-reply_generation.md](./02-reply_generation.md)
- [03-conversation_segmentation.md](./03-conversation_segmentation.md)
- [04-user_profile_analysis.md](./04-user_profile_analysis.md)
- [05-agent-development-guide.md](./05-agent-development-guide.md)
- [06-node-first-visual-composition.md](./06-node-first-visual-composition.md)

## 1. 推进原则

- 每个阶段只做一类收敛，不跨阶段抢跑。
- 通用契约、节点骨架和验收口径必须先串行统一。
- 四个 workflow 的 Prompt、节点配置和测试样例在通用骨架稳定后再并行。
- 子代理只在明确可并行阶段启动；并行前必须给每个子代理一份固定输入包。
- 每个阶段完成后先验收、再提交，再进入下一阶段。
- Webhook 异步结果回收、Dify streaming、前端配置台不进入本轮主线。

## 2. 阶段总览

| 阶段 | 名称 | 执行方式 | 是否启用子代理 | 目标 |
| --- | --- | --- | --- | --- |
| S0 | 版本与环境基线 | 串行 | 否 | 确认分支、文档、Dify 环境和密钥不混乱 |
| S1 | 契约冻结与测试夹具 | 串行 | 否 | 冻结 API blocking、payload、outputs 和验收样例 |
| S2 | 通用节点骨架打样 | 串行 | 否 | 做出一个可复用的节点优先 workflow 骨架 |
| S3 | 四个 workflow 并行配置 | 并行 | 是 | 分别完成四个 Dify workflow 的功能配置 |
| S4 | 统一验收与差异修正 | 串行，可短并行检查 | 视情况 | 跑 blocking API，统一清理字段漂移 |
| S5 | 自建系统接入确认 | 串行 | 否 | 确认系统侧调用、schema、ai_runs 和配置可消费正式 outputs |
| S6 | 端到端演练与交付封版 | 串行 | 否 | 用真实链路或准真实链路完成闭环验收 |
| S7 | 后续增强池 | 后续单独排期 | 否 | 放置异步 webhook、streaming、可视化配置台等增强 |

## 3. S0：版本与环境基线

执行方式：串行。

目标：

- 确认当前 Git 分支干净。
- 确认 Dify 功能文档已经进入项目内版本。
- 确认本地敏感文件不入库。
- 确认后续实施使用 API blocking，不回退到 Webhook 主链路。

实施步骤：

1. 运行 `git status --short --branch`，确认当前分支和工作区状态。
2. 阅读 `docs/dify-development/README.md` 和 `06-node-first-visual-composition.md`。
3. 检查 `.gitignore` 是否继续忽略 `.venv/`、`data/`、`output/`、`tests/dify_local_*.json`。
4. 确认 Dify 本地或目标环境可用，并单独记录 API base URL、四个 workflow 的 API key 存放位置。
5. 不把 API key、webhook URL、测试结果 JSON 提交入库。

交付物：

- 一条阶段记录，说明分支、Dify 环境、密钥存放方式、当前不启用 Webhook 主链路。

验收：

- 工作区干净或只包含本阶段文档改动。
- 没有新增敏感文件进入 Git 暂存区。

## 4. S1：契约冻结与测试夹具

执行方式：串行。

目标：

- 把四个 workflow 的输入 payload、End outputs、兜底输出和验收样例冻结。
- 为后续 Dify 配置准备统一测试夹具。

实施步骤：

1. 对照四个 workflow 文档，确认 Start 只声明 `payload: json_object`。
2. 对照 `src/app/dify/schemas.py`，确认系统侧 schema 与文档字段一致。
3. 为四个 workflow 准备三类测试 payload：成功样例、空输入或弱相关样例、非法输出风险样例。
4. 统一测试结果判定：只看 blocking API 的 `data.status` 和 `data.outputs`。
5. 明确禁止临时字段：`cs`、`huihua`、`ceshiziduan`、`result_text`。

交付物：

- 四个 workflow 的测试 payload 清单。
- 一份验收口径说明：请求体、期望输出、失败判定。

验收：

- 每个 workflow 至少有成功、兜底、非法输出防护三类样例。
- 样例中不编造输入不存在的 `msgid` 和 `userid`。

## 5. S2：通用节点骨架打样

执行方式：串行。

目标：

- 在 Dify 中先打通一个节点优先的参考 workflow 骨架。
- 参考骨架通过后，四个 workflow 才能并行展开。

建议选择：

- 首选 `reply_generation` 做骨架打样，因为它字段最多、输出最完整，也最容易暴露 End outputs 和兜底字段问题。

参考拓扑：

```text
Start(payload)
  -> Code: normalize_reply_input
  -> If/Else: can_reply
    true  -> Template Transform: build_reply_context
          -> Parameter Extractor: extract_reply_request (optional)
          -> LLM: generate_reply
          -> Code: finalize_reply
          -> Variable Aggregator: reply_outputs
          -> End(action, reply, metadata, confidence)
    false -> Variable Aggregator: reply_outputs
          -> End(action, reply, metadata, confidence)
```

实施步骤：

1. 在 Dify 创建或复制测试 workflow。
2. 配置 Start，仅保留 `payload: json_object`。
3. 配置 normalize Code，只做字段读取、默认值、合法集合、兜底字段。
4. 配置 If/Else，输入不足时跳过 LLM。
5. 配置 Template Transform，拼装 LLM 上下文。
6. 配置 LLM，先使用低温度，要求只输出 JSON。
7. 配置 finalize Code，解析 JSON、校验 schema、校验证据、输出兜底。
8. 配置 Variable Aggregator 或等价兜底汇合方式。
9. 配置 End，只声明正式业务字段。
10. 用 blocking API 跑成功样例和兜底样例。

交付物：

- 一个可运行的参考 workflow。
- 可复用的 normalize/finalize Code 模板。
- 可复用的 Template Transform 模板结构。
- 一份“节点配置注意事项”补充到开发记录。

验收：

- API 返回 `data.status=succeeded`。
- `data.outputs` 直接是正式字段结构。
- 画布中能看出输入门禁、上下文拼装、业务推理、校验兜底、结果汇合。

## 6. S3：四个 workflow 并行配置

执行方式：并行。

启用子代理：是。仅在 S2 骨架验收完成后启用。

并行前固定输入包：

- 本文档。
- 对应 workflow 规格文档。
- `00-api-blocking-node-skeleton.md`。
- `06-node-first-visual-composition.md`。
- S2 产出的参考 workflow 和可复用模板。
- S1 产出的测试 payload。

并行任务拆分：

| 子代理 | 范围 | 主要交付物 | 独立性 |
| --- | --- | --- | --- |
| Agent A | `intent_detection` | 节点流、Prompt、finalize Code、三类 API 测试结果 | 只依赖通用骨架和输入契约 |
| Agent B | `reply_generation` | 节点流、Prompt、可选 Parameter Extractor、finalize Code、三类 API 测试结果 | 可复用 S2 骨架继续深化 |
| Agent C | `conversation_segmentation` | 节点流、Prompt、边界校验、三类 API 测试结果 | 与其他 workflow 无共享状态 |
| Agent D | `user_profile_analysis` | 节点流、Prompt、画像事实校验、三类 API 测试结果 | 与其他 workflow 无共享状态 |

每个子代理必须完成：

1. 按对应文档配置 Start、normalize Code、If/Else、Template Transform、LLM、finalize Code、Variable Aggregator、End。
2. 检查 End outputs 字段名和字段类型。
3. 跑成功样例。
4. 跑兜底样例。
5. 跑非法输出防护样例。
6. 记录 `data.outputs` 原文。
7. 记录是否出现临时字段。

并行禁止事项：

- 不修改其他 workflow 的字段契约。
- 不新增自建系统职责到 Dify。
- 不把 Webhook ack 当作业务结果。
- 不把 API key、测试结果密钥文件提交入库。

合并顺序：

1. 先收 Agent B 的 `reply_generation`，因为它影响最终可发送正文边界。
2. 再收 Agent A 的 `intent_detection`，确认它只输出 `reply_instruction`。
3. 再收 Agent C 和 Agent D。
4. 最后统一检查四个 workflow 的输出结构和兜底一致性。

## 7. S4：统一验收与差异修正

执行方式：串行，可短并行检查。

目标：

- 把 S3 并行结果收敛成统一口径。
- 消除字段漂移、临时字段、兜底不一致和证据引用问题。

实施步骤：

1. 逐个 workflow 用 blocking API 跑成功样例。
2. 逐个 workflow 用 blocking API 跑兜底样例。
3. 逐个 workflow 跑非法输出防护样例。
4. 检查 `data.outputs` 是否直接包含正式字段。
5. 检查所有 `msgid` 和 `userid` 是否来自输入。
6. 检查 `intent_detection` 是否没有最终回复正文。
7. 检查只有 `reply_generation` 输出可发送正文。
8. 把失败项退回对应 workflow 修正。

可短并行项：

- 一个子代理检查四个 workflow 的输出字段。
- 一个子代理检查四个 workflow 的证据合法性。
- 一个子代理检查 Dify 画布节点是否符合官方节点优先拓扑。

这三个检查只读结果，不直接改配置；修正仍由主流程统一安排，避免并行互相覆盖。

交付物：

- 四个 workflow 的 blocking API 验收记录。
- 一份差异修正清单。

验收：

- 所有 workflow 返回 `data.status=succeeded`。
- 所有 workflow 的 `data.outputs` 无临时字段。
- 所有兜底输出符合对应文档。

## 8. S5：自建系统接入确认

执行方式：串行。

目标：

- 确认自建系统以 `inputs.payload` 调用 Dify API blocking。
- 确认业务侧继续校验 JSON schema、记录 ai_runs、处理 invalid_output。

实施步骤：

1. 检查 `DifyHttpClient` 请求体是否为：

```json
{
  "inputs": {
    "payload": {}
  },
  "response_mode": "blocking",
  "user": "system-or-operator"
}
```

2. 检查 `ai_workflows.input_schema` 是否校验业务 payload，不校验 Dify 外层包装。
3. 检查 `ai_workflows.output_schema` 是否校验 Dify `data.outputs`。
4. 用 mock client 跑现有单元测试。
5. 用真实 Dify 配置跑一组集成测试或手工 API 检查。
6. 确认 invalid output 不会进入后续业务发送、写库或画像更新。

交付物：

- 系统侧接入检查记录。
- 必要时提交系统侧小修正。

验收：

- 现有测试通过。
- 真实 Dify blocking 返回可被系统消费。
- 失败或非法输出可被系统标记为失败或 invalid_output。

## 9. S6：端到端演练与交付封版

执行方式：串行。

目标：

- 用接近真实的链路验证 Dify 功能能被业务闭环消费。

演练链路：

- 显式 @ 消息：入库 -> `reply_generation` -> outbox。
- 关键词或定时扫描：消息窗口 -> `intent_detection` -> 必要时 `reply_generation` -> outbox。
- 会话窗口：消息窗口 -> `conversation_segmentation` -> 自建系统生成会话记录。
- 用户画像：近期消息和会话摘要 -> `user_profile_analysis` -> 自建系统生成画像建议或新版本。

验收：

- Dify 不直接发送企微消息。
- Dify 不写业务库。
- 自建系统能记录 ai_runs、消费 outputs、处理失败。
- 输出证据引用可追溯到输入。

封版动作：

1. 更新 Dify 开发文档中的实施状态。
2. 提交最终配置记录和验收记录。
3. 给出下一轮增强建议。

## 10. S7：后续增强池

以下事项不进入本轮主线，后续单独排期：

- Webhook 异步结果回收机制。
- Dify streaming 或流式回复体验。
- Dify 工作流配置导出、导入和版本管理自动化。
- 后台可视化配置台。
- 更细的 prompt A/B 测试和模型成本统计。
- 长上下文压缩、RAG 知识库、工具节点或外部 HTTP Request 节点。

## 11. 子代理启用规则

只有满足以下条件，才启动子代理并行：

- S1 契约冻结完成。
- S2 参考 workflow 骨架通过 blocking API。
- 每个 workflow 的输入样例和兜底样例已经固定。
- 每个子代理的修改范围单独限定在一个 workflow。
- 主流程有统一验收人负责合并结果。

推荐子代理任务描述格式：

```text
请只实现 <workflow_code> 的 Dify workflow 配置。
必须遵守 docs/dify-development/00、对应 workflow 文档、06 节点优先文档。
不得修改其他 workflow 字段契约。
交付：节点配置说明、Prompt、normalize/finalize Code、三类 blocking API 结果、是否存在临时字段。
```

## 12. 当前下一步

下一步应进入 S0，然后按 S1、S2 串行推进。不要现在直接并行四个 workflow；并行点在 S3。
