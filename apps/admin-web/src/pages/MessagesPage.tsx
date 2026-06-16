import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { ErrorPanel } from "../components/ErrorPanel";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime, today } from "../utils/date";

const LIMIT = 50;

export function MessagesPage() {
  const [startDate, setStartDate] = useState(today());
  const [endDate, setEndDate] = useState(today());
  const [q, setQ] = useState("");
  const [chatid, setChatid] = useState("");
  const [senderType, setSenderType] = useState("");
  const [mentionedBot, setMentionedBot] = useState("");
  const [includeSourceDuplicates, setIncludeSourceDuplicates] = useState(false);
  const [offset, setOffset] = useState(0);
  const params = useMemo(
    () => ({
      start_date: startDate,
      end_date: endDate,
      chatid,
      sender_type: senderType,
      mentioned_bot: mentionedBot,
      include_source_duplicates: includeSourceDuplicates,
      q,
      limit: LIMIT,
      offset
    }),
    [chatid, endDate, includeSourceDuplicates, mentionedBot, offset, q, senderType, startDate]
  );
  const query = useQuery({
    queryKey: ["messages", params],
    queryFn: () => api.messages(params),
    placeholderData: keepPreviousData,
    staleTime: 30_000
  });

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>完整消息</h1>
          <p>按日期范围查看用户消息和已发送机器人消息，最多查询一个月。</p>
        </div>
      </header>
      <div className="filter-bar">
        <label>
          开始日期
          <input
            type="date"
            value={startDate}
            onChange={(event) => {
              setOffset(0);
              setStartDate(event.target.value);
            }}
          />
        </label>
        <label>
          截止日期
          <input
            type="date"
            value={endDate}
            onChange={(event) => {
              setOffset(0);
              setEndDate(event.target.value);
            }}
          />
        </label>
        <label>
          群 ID
          <input
            value={chatid}
            onChange={(event) => {
              setOffset(0);
              setChatid(event.target.value);
            }}
          />
        </label>
        <label>
          发送者
          <select
            value={senderType}
            onChange={(event) => {
              setOffset(0);
              setSenderType(event.target.value);
            }}
          >
            <option value="">全部</option>
            <option value="user">用户</option>
            <option value="bot">机器人</option>
          </select>
        </label>
        <label>
          @ 机器人
          <select
            value={mentionedBot}
            onChange={(event) => {
              setOffset(0);
              setMentionedBot(event.target.value);
            }}
          >
            <option value="">全部</option>
            <option value="true">是</option>
            <option value="false">否</option>
          </select>
        </label>
        <label className="search-label">
          搜索正文
          <input
            value={q}
            onChange={(event) => {
              setOffset(0);
              setQ(event.target.value);
            }}
          />
        </label>
        <label className="checkbox-row filter-checkbox">
          <input
            type="checkbox"
            checked={includeSourceDuplicates}
            onChange={(event) => {
              setOffset(0);
              setIncludeSourceDuplicates(event.target.checked);
            }}
          />
          显示来源重复记录
        </label>
      </div>
      <ErrorPanel error={query.error} />
      <section className="panel">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>群</th>
              <th>用户</th>
              <th>类型</th>
              <th>@</th>
              <th>归并</th>
              <th>内容</th>
              <th>msgid</th>
            </tr>
          </thead>
          <tbody>
            {(query.data?.items ?? []).map((message) => (
              <tr key={message.id}>
                <td>{compactTime(message.create_time)}</td>
                <td>{message.chatid}</td>
                <td>{message.userid ?? message.bot_role ?? "-"}</td>
                <td>{message.sender_type ?? "-"}</td>
                <td>{message.mentioned_bot ? <StatusBadge status="success" /> : "-"}</td>
                <td>
                  {message.is_business_duplicate ? (
                    <StatusBadge status="source_duplicate" />
                  ) : (
                    <StatusBadge status="none" />
                  )}
                </td>
                <td className="message-cell">{message.content_text ?? ""}</td>
                <td className="mono">{message.external_msgid}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <Pagination
        total={query.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffsetChange={setOffset}
      />
    </section>
  );
}
