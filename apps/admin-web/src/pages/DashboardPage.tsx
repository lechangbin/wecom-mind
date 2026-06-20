import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorPanel } from "../components/ErrorPanel";
import { StatusBadge } from "../components/StatusBadge";
import { today } from "../utils/date";

export function DashboardPage() {
  const date = today();
  const overview = useQuery({
    queryKey: ["dashboard", date],
    queryFn: () => api.dashboard(date),
    staleTime: 20_000
  });
  const workflows = useQuery({
    queryKey: ["workflowStats"],
    queryFn: () => api.workflows(),
    staleTime: 20_000
  });
  const fullTest = useQuery({
    queryKey: ["fullTestStatus"],
    queryFn: () => api.fullTestStatus(),
    refetchInterval: 10_000
  });
  const workers = useQuery({
    queryKey: ["systemWorkers"],
    queryFn: () => api.workers(),
    refetchInterval: 15_000
  });

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>总览</h1>
          <p>当天运行状态、AI 调用和待处理发送概览。</p>
        </div>
      </header>

      <ErrorPanel error={overview.error || workflows.error || fullTest.error || workers.error} />

      <div className="metric-grid">
        <Metric title="今日消息" value={overview.data?.today_message_count ?? "-"} />
        <Metric title="活跃群聊" value={overview.data?.active_chat_count ?? "-"} />
        <Metric title="今日触发" value={overview.data?.today_trigger_count ?? "-"} />
        <Metric title="AI 成功率" value={`${Math.round((overview.data?.ai_success_rate ?? 0) * 100)}%`} />
        <Metric title="待发送" value={overview.data?.pending_outbox_count ?? "-"} />
        <Metric title="发送异常" value={overview.data?.failed_outbox_count ?? "-"} />
      </div>

      <div className="content-split">
        <section className="panel">
          <div className="panel-title">工作流</div>
          <table>
            <thead>
              <tr>
                <th>workflow</th>
                <th>总数</th>
                <th>成功率</th>
                <th>平均耗时</th>
              </tr>
            </thead>
            <tbody>
              {(workflows.data?.items ?? []).map((item) => (
                <tr key={item.workflow_code}>
                  <td>{item.workflow_code}</td>
                  <td>{item.total}</td>
                  <td>{Math.round(item.success_rate * 100)}%</td>
                  <td>{item.avg_latency_ms ?? "-"} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="panel">
          <div className="panel-title">AI memory full-test</div>
          <div className="kv-list">
            <span>状态</span>
            <StatusBadge status={fullTest.data?.status ?? "idle"} />
            <span>最近错误</span>
            <strong>{fullTest.data?.last_error ?? "-"}</strong>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-title">Worker 状态</div>
        <table>
          <thead>
            <tr>
              <th>模块</th>
              <th>启用</th>
              <th>状态</th>
              <th>观测方式</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {(workers.data?.items ?? []).map((worker) => (
              <tr key={worker.worker_key}>
                <td>{worker.title}</td>
                <td>{worker.enabled ? "是" : "否"}</td>
                <td>
                  <StatusBadge status={worker.status} />
                </td>
                <td>{worker.observable ? "进程内" : "配置推断"}</td>
                <td>{workerDetail(worker.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}

function Metric({ title, value }: { title: string; value: string | number }) {
  return (
    <div className="metric">
      <span>{title}</span>
      <strong>{value}</strong>
    </div>
  );
}

function workerDetail(details: Record<string, unknown>) {
  const parts = Object.entries(details)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${key}: ${String(value)}`);
  return parts.length ? parts.join(" / ") : "-";
}
