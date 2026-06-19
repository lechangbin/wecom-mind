import type {
  ApiEnvelope,
  AdminSession,
  AiRunItem,
  ConversationItem,
  ConversationSegmentRunResult,
  DashboardOverview,
  FullTestStatus,
  MessageItem,
  OutboxItem,
  Page,
  RecentProfileGenerationResult,
  ReplyTask,
  SystemConfigSummary,
  UserItem,
  UserProfile,
  WorkerStatus,
  WorkflowStat
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export type QueryParams = Record<string, string | number | boolean | null | undefined>;

export class ApiError extends Error {
  code: string;
  details: unknown;

  constructor(code: string, message: string, details?: unknown) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

export async function apiGet<T>(path: string, params?: QueryParams): Promise<T> {
  const response = await fetch(`${API_BASE}${path}${queryString(params)}`, {
    credentials: "include"
  });
  return unwrap<T>(response);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  return unwrap<T>(response);
}

export function adminEventUrl(): string {
  return `${API_BASE}/api/admin/events/stream`;
}

export const api = {
  adminMe: () => apiGet<AdminSession>("/api/admin/auth/me"),
  adminLogin: (body: { username: string; password: string }) =>
    apiPost<AdminSession>("/api/admin/auth/login", body),
  adminLogout: () => apiPost<AdminSession>("/api/admin/auth/logout", {}),
  health: () => apiGet<Record<string, unknown>>("/health"),
  configSummary: () => apiGet<SystemConfigSummary>("/api/system/config-summary"),
  workers: () => apiGet<{ items: WorkerStatus[] }>("/api/system/workers"),
  dashboard: (date?: string) =>
    apiGet<DashboardOverview>("/api/dashboard/overview", { date }),
  workflows: () => apiGet<{ items: WorkflowStat[] }>("/api/stats/workflows"),
  replyTasks: (params: QueryParams) =>
    apiGet<Page<ReplyTask>>("/api/admin/reply-tasks", params),
  messages: (params: QueryParams) => apiGet<Page<MessageItem>>("/api/messages", params),
  aiRuns: (params: QueryParams) => apiGet<Page<AiRunItem>>("/api/ai-runs", params),
  aiRun: (runId: string) => apiGet<AiRunItem>(`/api/ai-runs/${runId}`),
  outboxMessages: (params: QueryParams) =>
    apiGet<Page<OutboxItem>>("/api/outbox-messages", params),
  outboxMessage: (outboxId: string) =>
    apiGet<OutboxItem>(`/api/outbox-messages/${outboxId}`),
  sendOutbox: (outboxId: string) =>
    apiPost<OutboxItem>(`/api/outbox-messages/${outboxId}/send`, {}),
  users: (params: QueryParams) => apiGet<Page<UserItem>>("/api/users", params),
  userProfile: (userid: string) => apiGet<UserProfile>(`/api/users/${userid}/profile`),
  userProfileVersions: (userid: string, params: QueryParams) =>
    apiGet<Page<UserProfile>>(`/api/users/${userid}/profile/versions`, params),
  generateUserProfile: (userid: string, body: { force?: boolean } = {}) =>
    apiPost<RecentProfileGenerationResult>(`/api/users/${userid}/profile/generate`, body),
  conversations: (params: QueryParams) =>
    apiGet<Page<ConversationItem>>("/api/conversations", params),
  runConversationSegment: (body: {
    chatid: string;
    start_time: string;
    end_time: string;
    mode?: string;
    force?: boolean;
  }) => apiPost<ConversationSegmentRunResult>("/api/conversations/segment/run", body),
  conversationMessages: (conversationNo: string) =>
    apiGet<Page<MessageItem>>(`/api/conversations/${conversationNo}/messages`),
  fullTestStatus: () => apiGet<FullTestStatus>("/api/ai-memory/full-test/status"),
  runFullTest: (body: {
    target_date: string;
    chatids: string[];
    run_profiles: boolean;
  }) => apiPost<Record<string, unknown>>("/api/ai-memory/full-test/run", body)
};

async function unwrap<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(
      payload.error?.code ?? String(response.status),
      payload.error?.message ?? response.statusText,
      payload.error?.details
    );
  }
  return payload.data;
}

function queryString(params?: QueryParams): string {
  if (!params) {
    return "";
  }
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}
