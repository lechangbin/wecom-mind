import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { ErrorPanel } from "../components/ErrorPanel";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime, today } from "../utils/date";

const LIMIT = 20;

export function ConversationsPage() {
  const [startDate, setStartDate] = useState(today());
  const [endDate, setEndDate] = useState(today());
  const [q, setQ] = useState("");
  const [chatid, setChatid] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      start_date: startDate,
      end_date: endDate,
      chatid,
      q,
      limit: LIMIT,
      offset
    }),
    [chatid, endDate, offset, q, startDate]
  );
  const query = useQuery({
    queryKey: ["conversations", params],
    queryFn: () => api.conversations(params),
    placeholderData: keepPreviousData,
    staleTime: 30_000
  });
  const detail = useQuery({
    queryKey: ["conversationMessages", selected],
    queryFn: () => api.conversationMessages(selected as string),
    enabled: Boolean(selected),
    staleTime: 60_000
  });

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>会话审查</h1>
          <p>查看切分成功的会话，搜索优先匹配 AI 摘要。</p>
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
          群 ID
          <input value={chatid} onChange={(event) => setChatid(event.target.value)} />
        </label>
        <label className="search-label">
          搜索
          <input
            placeholder="摘要或会话内消息"
            value={q}
            onChange={(event) => {
              setOffset(0);
              setQ(event.target.value);
            }}
          />
        </label>
      </div>
      <ErrorPanel error={query.error || detail.error} />
      <div className="content-split wide-left">
        <section className="panel">
          <table>
            <thead>
              <tr>
                <th>开始时间</th>
                <th>标题</th>
                <th>摘要</th>
                <th>命中</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((conversation) => (
                <tr
                  className={conversation.conversation_no === selected ? "selected-row" : ""}
                  key={conversation.conversation_no}
                  onClick={() => setSelected(conversation.conversation_no)}
                >
                  <td>{compactTime(conversation.start_time)}</td>
                  <td>{conversation.title}</td>
                  <td className="message-cell">{conversation.summary}</td>
                  <td>
                    <StatusBadge status={conversation.match_source} />
                  </td>
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
        <section className="panel detail-panel">
          <div className="panel-title">会话消息</div>
          {!selected ? <p className="muted">选择一条会话查看原始消息。</p> : null}
          {(detail.data?.items ?? []).map((message) => (
            <div className="message-detail" key={message.id}>
              <div>
                <strong>{message.userid ?? "-"}</strong>
                <span>{compactTime(message.create_time)}</span>
              </div>
              <p>{message.content_text}</p>
            </div>
          ))}
        </section>
      </div>
    </section>
  );
}
