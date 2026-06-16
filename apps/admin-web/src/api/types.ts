export type ApiEnvelope<T> = {
  success: boolean;
  request_id: string;
  data: T;
  error: null | {
    code: string;
    message: string;
    details?: unknown;
  };
};

export type Page<T> = {
  total: number;
  limit?: number;
  offset?: number;
  items: T[];
};

export type AdminSession = {
  auth_enabled: boolean;
  authenticated: boolean;
  username: string | null;
};

export type MessageItem = {
  id: number | string;
  external_msgid: string;
  chatid: string;
  chattype?: string;
  userid: string | null;
  msgtype: string;
  sender_type?: string;
  bot_role?: string | null;
  content_text: string | null;
  mentioned_bot: boolean;
  mentioned_users?: unknown[];
  quote_msgid: string | null;
  business_identity_key?: string | null;
  canonical_message_id?: number | null;
  is_business_duplicate?: boolean;
  create_time: string;
  created_at?: string;
};

export type ReplyNode = {
  node_key: string;
  title: string;
  status: "pending" | "running" | "success" | "error" | "skipped";
  detail: string;
};

export type ReplyTask = {
  task_id: string;
  task_type: "mention" | "proactive";
  status: "replying" | "replied" | "error";
  chatid: string;
  source_message: Partial<MessageItem>;
  nodes: ReplyNode[];
  ai_run_id: string | null;
  outbox_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type ConversationItem = {
  id: number;
  conversation_no: string;
  chatid: string;
  start_time: string;
  end_time: string;
  title: string;
  summary: string;
  keywords: string[];
  participants: string[];
  confidence: number;
  status: string;
  match_source: "summary" | "message" | "none";
};

export type DashboardOverview = {
  date: string;
  today_message_count: number;
  total_message_count: number;
  active_chat_count: number;
  active_user_count: number;
  today_trigger_count: number;
  ai_run_count: number;
  ai_success_rate: number;
  avg_ai_latency_ms: number | null;
  pending_outbox_count: number;
  failed_outbox_count: number;
  scheduled_intent_count: number;
};

export type WorkflowStat = {
  workflow_code: string;
  total: number;
  success: number;
  failed: number;
  invalid_output: number;
  success_rate: number;
  avg_latency_ms: number | null;
};

export type AiRunItem = {
  id: number;
  run_id: string;
  workflow_code: string;
  workflow_version: string;
  trigger_event_id: number | null;
  input_json: unknown;
  output_json: unknown;
  response_mode: string;
  status: string;
  latency_ms: number | null;
  token_usage: unknown;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type OutboxItem = {
  id: number;
  outbox_id: string;
  scene: string;
  chatid: string;
  target_userids: unknown[];
  msgtype: string;
  content: Record<string, unknown>;
  source_type: string;
  source_id: string;
  status: string;
  retry_count: number;
  external_msgid: string | null;
  error_code: string | null;
  error_message: string | null;
  raw_response: unknown;
  idempotency_key: string;
  scheduled_at: string | null;
  sent_at: string | null;
  created_at: string;
  duplicated?: boolean;
};

export type UserItem = {
  id: number;
  userid: string;
  name: string | null;
  status: string;
  last_active_at: string | null;
  message_count: number;
  latest_profile_summary: string | null;
};

export type UserProfileFact = {
  id: number;
  userid: string;
  profile_id: number;
  source_ai_run_id: number;
  fact_type: string;
  label: string;
  description: string;
  evidence_msgids: unknown[];
  evidence_conversation_nos: unknown[];
  confidence: number;
  status: string;
  created_at: string;
};

export type UserProfile = {
  id: number;
  userid: string;
  summary: string;
  profile_json: Record<string, unknown>;
  ai_run_id: number;
  confidence: number;
  version: number;
  status: string;
  facts: UserProfileFact[];
  last_analyzed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type SystemConfigSummary = {
  app: {
    app_env: string;
    app_base_url: string;
    admin_web_host: string;
    admin_web_port: number;
    admin_web_url: string;
    log_level: string;
  };
  database: {
    kind: string;
    configured: boolean;
  };
  redis: {
    configured: boolean;
  };
  wecom: {
    sender_mode: string;
    verify_mode: string;
    reply_bot_configured: boolean;
    intent_bot_configured: boolean;
    message_reconcile_enabled: boolean;
    message_reconcile_chatids_count: number;
    message_reconcile_auto_enqueue: boolean;
    message_reconcile_auto_send: boolean;
    timeout_seconds: number;
    max_retries: number;
  };
  dify: {
    client_mode: string;
    base_url_configured: boolean;
    timeout_seconds: number;
    max_retries: number;
    user: string;
    workflow_api_keys: Record<string, boolean>;
  };
  ai_memory: {
    enabled: boolean;
    chatids_count: number;
    interval_seconds: number;
    days_back: number;
    run_profiles: boolean;
  };
};

export type WorkerStatus = {
  worker_key: string;
  title: string;
  enabled: boolean;
  status: string;
  observable: boolean;
  details: Record<string, unknown>;
};

export type FullTestStatus = {
  status: "idle" | "running" | "succeeded" | "failed";
  current: null | Record<string, unknown>;
  last_result: null | Record<string, unknown>;
  last_error: null | string;
};

export type AdminEvent = {
  event_id: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  payload: unknown;
  created_at: string;
};
