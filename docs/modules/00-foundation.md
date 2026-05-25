# 00. 配置与基础设施模块

## 1. 模块职责

配置与基础设施模块为所有业务模块提供统一基础能力。

负责：

- 应用配置读取。
- 数据库连接。
- 日志。
- 统一错误码。
- 请求 ID。
- 任务状态枚举。
- JSON schema 校验工具。
- 外部 API 超时与重试基础封装。

不负责：

- 具体企微 API 业务逻辑。
- 具体 Dify 工作流逻辑。
- 具体消息处理业务。

## 2. 关键配置

建议预留以下配置项：

```text
APP_ENV
APP_BASE_URL
DATABASE_URL
REDIS_URL

WECOM_CORP_ID
WECOM_MCP_TOKEN
WECOM_MCP_ENCODING_AES_KEY
WECOM_AIBOT_ID
WECOM_AIBOT_SECRET
WECOM_AIBOT_WS_URL

DIFY_BASE_URL
DIFY_API_KEY

LOG_LEVEL
```

如果 MVP 暂时不使用 Redis，可以先用数据库任务表实现异步任务。

## 3. 核心能力

### 3.1 统一请求 ID

所有入口请求生成 `request_id`。

用途：

- 回调日志串联。
- AI 调用日志串联。
- 发送失败排查。

### 3.2 统一错误码

错误码参考：

- `INVALID_ARGUMENT`
- `UNAUTHORIZED`
- `FORBIDDEN`
- `NOT_FOUND`
- `DUPLICATED`
- `EXTERNAL_API_ERROR`
- `DIFY_OUTPUT_INVALID`
- `WECOM_SEND_FAILED`
- `INTERNAL_ERROR`

### 3.3 JSON schema 校验

用于：

- 校验 Dify 输出。
- 校验配置中的触发规则。
- 校验发送内容。

## 4. 建议内部目录

```text
core/
  errors
  logging
  request_context
  schema_validator
  retry
config/
  settings
db/
  connection
  migrations
```

## 5. MVP 验收标准

- 能读取本地环境配置。
- 能连接数据库。
- 所有 API 响应带 `request_id`。
- 有统一错误响应格式。
- 有可复用 JSON schema 校验工具。
- 日志不会输出企微 secret、Dify API key 或 access_token。
