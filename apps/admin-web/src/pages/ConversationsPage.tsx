import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, RefreshCcw, Scissors, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { ErrorPanel } from "../components/ErrorPanel";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime, today } from "../utils/date";

const LIMIT = 20;

export function ConversationsPage() {
  const queryClient = useQueryClient();
  const [startDate, setStartDate] = useState(today());
  const [endDate, setEndDate] = useState(today());
  const [q, setQ] = useState("");
  const [chatid, setChatid] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [segmentDate, setSegmentDate] = useState(today());
  const [segmentDialogOpen, setSegmentDialogOpen] = useState(false);
  const [segmentNotice, setSegmentNotice] = useState("");
  const [defaultChatidApplied, setDefaultChatidApplied] = useState(false);
  const config = useQuery({
    queryKey: ["configSummary"],
    queryFn: () => api.configSummary(),
    staleTime: 60_000
  });
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
  useEffect(() => {
    const defaultChatid = config.data?.wecom.default_chatid;
    if (!defaultChatidApplied && !chatid.trim() && defaultChatid) {
      setChatid(defaultChatid);
      setDefaultChatidApplied(true);
    }
  }, [chatid, config.data?.wecom.default_chatid, defaultChatidApplied]);
  const segmentMutation = useMutation({
    mutationFn: (force: boolean) => {
      const targetChatid = chatid.trim();
      if (!targetChatid) {
        throw new Error("请先填写群 ID");
      }
      const window = dayWindow(segmentDate || today());
      return api.runConversationSegment({
        chatid: targetChatid,
        start_time: window.start,
        end_time: window.end,
        mode: "manual",
        force
      });
    },
    onSuccess: (result) => {
      setSegmentDialogOpen(false);
      if (result.force) {
        setSegmentNotice(
          `已强制重切 ${result.segments.length} 条会话，替换 ${result.superseded_count} 条旧切片`
        );
      } else if (result.status === "reused") {
        setSegmentNotice(`已检查 ${result.segments.length} 条会话，当前日期已有切分结果`);
      } else {
        setSegmentNotice(`已生成 ${result.segments.length} 条会话，状态 ${result.status}`);
      }
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  });

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>会话审查</h1>
          <p>查看切分成功的会话，搜索优先匹配 AI 摘要，最多查询一个月。</p>
        </div>
        <button
          className="primary-button"
          type="button"
          onClick={() => {
            setSegmentDate(startDate || today());
            setSegmentNotice("");
            setSegmentDialogOpen(true);
          }}
        >
          <Scissors size={16} />
          手动切分
        </button>
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
      <ErrorPanel error={query.error || detail.error || config.error || segmentMutation.error} />
      {segmentNotice ? <div className="notice-panel">{segmentNotice}</div> : null}
      {segmentDialogOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="segment-dialog-title">
            <div className="modal-title-row">
              <div>
                <h2 id="segment-dialog-title">手动切分</h2>
                <p className="muted mono">{chatid.trim() || "未选择群 ID"}</p>
              </div>
              <button type="button" title="关闭" onClick={() => setSegmentDialogOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <label>
              日期
              <input
                type="date"
                value={segmentDate}
                onChange={(event) => setSegmentDate(event.target.value)}
              />
            </label>
            <div className="modal-action-row">
              <button type="button" onClick={() => setSegmentDialogOpen(false)}>
                取消
              </button>
              <button
                className="primary-button"
                type="button"
                disabled={segmentMutation.isPending || !chatid.trim() || !segmentDate}
                onClick={() => segmentMutation.mutate(false)}
              >
                <CalendarDays size={16} />
                {segmentMutation.isPending ? "切分中" : "开始切分"}
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={segmentMutation.isPending || !chatid.trim() || !segmentDate}
                onClick={() => segmentMutation.mutate(true)}
              >
                <RefreshCcw size={16} />
                强制重切
              </button>
            </div>
          </section>
        </div>
      ) : null}
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

function dayWindow(date: string): { start: string; end: string } {
  return {
    start: `${date}T00:00:00+08:00`,
    end: `${date}T23:59:59.999+08:00`
  };
}
