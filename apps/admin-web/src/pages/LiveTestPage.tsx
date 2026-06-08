import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "../api/client";
import { ErrorPanel } from "../components/ErrorPanel";
import { JsonBlock } from "../components/JsonBlock";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime, today } from "../utils/date";

export function LiveTestPage() {
  const queryClient = useQueryClient();
  const [targetDate, setTargetDate] = useState(today());
  const [chatids, setChatids] = useState("");
  const [runProfiles, setRunProfiles] = useState(true);
  const status = useQuery({
    queryKey: ["fullTestStatus"],
    queryFn: () => api.fullTestStatus(),
    refetchInterval: 5000
  });
  const config = useQuery({
    queryKey: ["configSummary"],
    queryFn: () => api.configSummary(),
    staleTime: 30_000
  });
  const aiRuns = useQuery({
    queryKey: ["liveAiRuns"],
    queryFn: () => api.aiRuns({ limit: 20, offset: 0 }),
    staleTime: 20_000
  });
  const failedOutbox = useQuery({
    queryKey: ["liveFailedOutbox"],
    queryFn: () => api.outboxMessages({ status: "failed", limit: 20, offset: 0 }),
    staleTime: 20_000
  });
  const mutation = useMutation({
    mutationFn: () =>
      api.runFullTest({
        target_date: targetDate,
        chatids: chatids
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        run_profiles: runProfiles
      }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["fullTestStatus"] });
    }
  });
  const running = status.data?.status === "running" || mutation.isPending;

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>实机测试</h1>
          <p>手动触发 AI memory full-test，同一时间只允许一个任务。</p>
        </div>
        <StatusBadge status={status.data?.status ?? "idle"} />
      </header>

      <div className="content-split">
        <div className="panel form-panel">
          <label>
            目标日期
            <input type="date" value={targetDate} onChange={(event) => setTargetDate(event.target.value)} />
          </label>
          <label>
            群 ID
            <input
              placeholder="多个 chatid 用英文逗号分隔；留空为配置默认"
              value={chatids}
              onChange={(event) => setChatids(event.target.value)}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={runProfiles}
              onChange={(event) => setRunProfiles(event.target.checked)}
            />
            更新画像
          </label>
          <button type="button" disabled={running} onClick={() => mutation.mutate()}>
            {running ? "运行中" : "开始测试"}
          </button>
        </div>

        <section className="panel">
          <div className="panel-title">当前配置</div>
          <div className="kv-list">
            <span>Dify</span>
            <strong>{config.data?.dify.client_mode ?? "-"}</strong>
            <span>企微发送</span>
            <strong>{config.data?.wecom.sender_mode ?? "-"}</strong>
            <span>补漏</span>
            <strong>{config.data?.wecom.message_reconcile_enabled ? "启用" : "未启用"}</strong>
            <span>补漏发送</span>
            <strong>{config.data?.wecom.message_reconcile_auto_send ? "自动发送" : "不自动发送"}</strong>
            <span>AI memory</span>
            <strong>{config.data?.ai_memory.enabled ? "启用" : "未启用"}</strong>
            <span>数据库</span>
            <strong>{config.data?.database.kind ?? "-"}</strong>
            <span>前端地址</span>
            <strong>{config.data?.app.admin_web_url ?? "-"}</strong>
          </div>
        </section>
      </div>

      <ErrorPanel error={mutation.error || status.error || config.error || aiRuns.error || failedOutbox.error} />
      {mutation.error instanceof ApiError && mutation.error.details ? (
        <section className="panel">
          <div className="panel-title">运行中任务</div>
          <JsonBlock value={mutation.error.details} />
        </section>
      ) : null}
      <section className="panel">
        <div className="panel-title">最近结果</div>
        <JsonBlock value={status.data?.last_result ?? status.data ?? {}} />
      </section>

      <section className="panel">
        <div className="panel-title">最近 20 条 AI run</div>
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>workflow</th>
              <th>状态</th>
              <th>耗时</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            {(aiRuns.data?.items ?? []).map((run) => (
              <tr key={run.run_id}>
                <td>{compactTime(run.created_at)}</td>
                <td>{run.workflow_code}</td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>{run.latency_ms ?? "-"} ms</td>
                <td>{run.error_message ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="panel-title">最近 20 条失败发送</div>
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>scene</th>
              <th>群聊</th>
              <th>正文</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            {(failedOutbox.data?.items ?? []).map((outbox) => (
              <tr key={outbox.outbox_id}>
                <td>{compactTime(outbox.created_at)}</td>
                <td>
                  <StatusBadge status={outbox.scene} />
                </td>
                <td>{outbox.chatid}</td>
                <td>{outboxText(outbox.content)}</td>
                <td>{outbox.error_message ?? outbox.error_code ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}

function outboxText(content: Record<string, unknown>) {
  const markdown = content.markdown as { content?: unknown } | undefined;
  if (typeof markdown?.content === "string") {
    return markdown.content;
  }
  const text = content.text as { content?: unknown } | undefined;
  if (typeof text?.content === "string") {
    return text.content;
  }
  return JSON.stringify(content);
}
