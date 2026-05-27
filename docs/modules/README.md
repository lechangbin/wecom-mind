# 模块实现文档索引

## 1. 文档定位

本目录面向主体系统实现。项目当前只有一个 agent 负责主体开发，因此这里不是多人分工文档，而是模块级开发导航。

每个模块文档回答：

- 模块负责什么。
- 模块不负责什么。
- 模块依赖哪些上游和下游。
- 核心输入输出是什么。
- MVP 必须实现哪些能力。
- 做到什么算验收通过。

## 2. 推荐实现顺序

1. [配置与基础设施模块](./00-foundation.md)
2. [企微接入模块](./01-wecom-adapter.md)
3. [消息处理模块](./02-message-ingestion.md)
4. [触发路由模块](./03-trigger-router.md)
5. [发送模块](./05-outbound-dispatcher.md)
6. [会话智能模块](./06-conversation-intelligence.md)
7. [用户画像模块](./07-user-profile.md)
8. [管理与统计模块](./08-admin-analytics.md)

这个顺序不是物理目录要求，但建议后续代码结构也尽量对齐。

## 3. 推荐后端目录映射

如果后端使用 Python/FastAPI，可参考：

```text
src/
  app/
    core/
    config/
    db/
    wecom/
    messages/
    triggers/
    dify/
    outbound/
    conversations/
    profiles/
    admin/
    jobs/
```

如果后端使用 Node.js/TypeScript，可参考：

```text
src/
  core/
  config/
  db/
  modules/
    wecom/
    messages/
    triggers/
    dify/
    outbound/
    conversations/
    profiles/
    admin/
  jobs/
```

## 4. 统一实现原则

- 外部回调先落库，再异步处理。
- 核心写入必须幂等。
- 外部 AI 输出必须校验后再写库。
- 发送消息必须先写 outbox。
- 每个模块都要有明确输入、输出和状态字段。
- MVP 阶段先闭环，再优化性能和前端体验。

## 5. 与架构文档关系

模块实现文档是架构文档的落地补充。

- 架构总览：[../architecture/README.md](../architecture/README.md)
- 数据模型：[../architecture/data-model.md](../architecture/data-model.md)
