import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { ErrorPanel } from "../components/ErrorPanel";
import { NodeFlow } from "../components/NodeFlow";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime, today } from "../utils/date";

const LIMIT = 20;

export function ReplyTasksPage() {
  const [startDate, setStartDate] = useState(today());
  const [endDate, setEndDate] = useState(today());
  const [status, setStatus] = useState("");
  const [taskType, setTaskType] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const params = useMemo(
    () => ({
      start_date: startDate,
      end_date: endDate,
      status,
      task_type: taskType,
      q,
      limit: LIMIT,
      offset
    }),
    [endDate, offset, q, startDate, status, taskType]
  );
  const query = useQuery({
    queryKey: ["replyTasks", params],
    queryFn: () => api.replyTasks(params),
    placeholderData: keepPreviousData,
    staleTime: 15_000
  });

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>响应消息</h1>
          <p>查看 @ 回复和非 @ 主动回复的实时节点状态。</p>
        </div>
      </header>
      <div className="filter-bar">
        <label>
          开始日期
          <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        </label>
        <label>
          截止日期
          <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </label>
        <label>
          状态
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部</option>
            <option value="replying">正在回复</option>
            <option value="replied">已回复</option>
            <option value="error">异常</option>
          </select>
        </label>
        <label>
          类型
          <select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
            <option value="">全部</option>
            <option value="mention">@ 回复</option>
            <option value="proactive">非 @ 主动回复</option>
          </select>
        </label>
        <label className="search-label">
          搜索
          <input
            placeholder="消息正文"
            value={q}
            onChange={(event) => {
              setOffset(0);
              setQ(event.target.value);
            }}
          />
        </label>
      </div>
      <ErrorPanel error={query.error} />
      <div className="list-stack">
        {(query.data?.items ?? []).map((task) => (
          <article className="reply-task" key={task.task_id}>
            <div className="reply-task-head">
              <div>
                <strong>{task.task_type === "mention" ? "@ 回复" : "主动回复"}</strong>
                <span>{task.source_message.content_text ?? "-"}</span>
              </div>
              <StatusBadge status={task.status} />
            </div>
            <NodeFlow nodes={task.nodes} />
            <div className="meta-row">
              <span>{task.chatid}</span>
              <span>{task.source_message.userid ?? "-"}</span>
              <span>{compactTime(task.updated_at)}</span>
              <span>{task.outbox_id ?? "-"}</span>
            </div>
            {task.error_message ? <div className="inline-error">{task.error_message}</div> : null}
          </article>
        ))}
      </div>
      <Pagination
        total={query.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffsetChange={setOffset}
      />
    </section>
  );
}
