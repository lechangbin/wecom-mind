import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { AiRunItem } from "../api/types";
import { ErrorPanel } from "../components/ErrorPanel";
import { JsonBlock } from "../components/JsonBlock";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime } from "../utils/date";

const LIMIT = 25;

export function AiRunsPage() {
  const [workflowCode, setWorkflowCode] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      workflow_code: workflowCode,
      status,
      limit: LIMIT,
      offset
    }),
    [offset, status, workflowCode]
  );
  const query = useQuery({
    queryKey: ["aiRuns", params],
    queryFn: () => api.aiRuns(params),
    placeholderData: keepPreviousData,
    staleTime: 20_000
  });
  const detail = useQuery({
    queryKey: ["aiRun", selectedRunId],
    queryFn: () => api.aiRun(selectedRunId as string),
    enabled: Boolean(selectedRunId),
    staleTime: 60_000
  });
  const selected = detail.data ?? query.data?.items.find((item) => item.run_id === selectedRunId);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>AI 运行记录</h1>
          <p>查看 Dify 调用输入、输出、耗时和错误。</p>
        </div>
      </header>
      <div className="filter-bar">
        <label>
          工作流
          <input
            placeholder="workflow_code"
            value={workflowCode}
            onChange={(event) => {
              setOffset(0);
              setWorkflowCode(event.target.value);
            }}
          />
        </label>
        <label>
          状态
          <select
            value={status}
            onChange={(event) => {
              setOffset(0);
              setStatus(event.target.value);
            }}
          >
            <option value="">全部</option>
            <option value="running">运行中</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="invalid_output">输出异常</option>
          </select>
        </label>
      </div>
      <ErrorPanel error={query.error || detail.error} />
      <div className="content-split">
        <section className="panel">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>工作流</th>
                <th>状态</th>
                <th>耗时</th>
                <th>run_id</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((run) => (
                <tr
                  className={run.run_id === selectedRunId ? "selected-row" : ""}
                  key={run.run_id}
                  onClick={() => setSelectedRunId(run.run_id)}
                >
                  <td>{compactTime(run.created_at)}</td>
                  <td>{run.workflow_code}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td>{formatLatency(run.latency_ms)}</td>
                  <td className="mono">{run.run_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination
            total={query.data?.total ?? 0}
            limit={LIMIT}
            offset={offset}
            onOffsetChange={setOffset}
          />
        </section>
        <AiRunDetail run={selected} />
      </div>
    </section>
  );
}

function AiRunDetail({ run }: { run?: AiRunItem }) {
  if (!run) {
    return (
      <section className="panel detail-panel">
        <div className="panel-title">运行详情</div>
        <p className="muted detail-empty">选择一条记录查看详情。</p>
      </section>
    );
  }
  return (
    <section className="panel detail-panel">
      <div className="panel-title">运行详情</div>
      <div className="kv-list">
        <span>run_id</span>
        <strong className="mono">{run.run_id}</strong>
        <span>工作流</span>
        <strong>{run.workflow_code}</strong>
        <span>状态</span>
        <StatusBadge status={run.status} />
        <span>耗时</span>
        <strong>{formatLatency(run.latency_ms)}</strong>
      </div>
      {run.error_message ? <div className="inline-error">{run.error_message}</div> : null}
      <div className="panel-title">输入 JSON</div>
      <JsonBlock value={run.input_json ?? {}} />
      <div className="panel-title">输出 JSON</div>
      <JsonBlock value={run.output_json ?? {}} />
    </section>
  );
}

function formatLatency(value: number | null) {
  return value === null || value === undefined ? "-" : `${value} ms`;
}
