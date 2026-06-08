import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { OutboxItem } from "../api/types";
import { ErrorPanel } from "../components/ErrorPanel";
import { JsonBlock } from "../components/JsonBlock";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime } from "../utils/date";

const LIMIT = 25;

export function OutboxPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [chatid, setChatid] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedOutboxId, setSelectedOutboxId] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      status,
      chatid,
      limit: LIMIT,
      offset
    }),
    [chatid, offset, status]
  );
  const query = useQuery({
    queryKey: ["outboxMessages", params],
    queryFn: () => api.outboxMessages(params),
    placeholderData: keepPreviousData,
    staleTime: 20_000
  });
  const detail = useQuery({
    queryKey: ["outboxMessage", selectedOutboxId],
    queryFn: () => api.outboxMessage(selectedOutboxId as string),
    enabled: Boolean(selectedOutboxId),
    staleTime: 60_000
  });
  const sendMutation = useMutation({
    mutationFn: (outboxId: string) => api.sendOutbox(outboxId),
    onSuccess: (item) => {
      setSelectedOutboxId(item.outbox_id);
      void queryClient.invalidateQueries({ queryKey: ["outboxMessages"] });
      void queryClient.invalidateQueries({ queryKey: ["outboxMessage", item.outbox_id] });
    }
  });
  const selected =
    detail.data ?? query.data?.items.find((item) => item.outbox_id === selectedOutboxId);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>发送记录</h1>
          <p>查看 Outbox 发送状态、内容和错误。</p>
        </div>
      </header>
      <div className="filter-bar">
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
            <option value="pending">待处理</option>
            <option value="sending">发送中</option>
            <option value="sent">已发送</option>
            <option value="failed">失败</option>
            <option value="canceled">已取消</option>
          </select>
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
      </div>
      <ErrorPanel error={query.error || detail.error || sendMutation.error} />
      <div className="content-split">
        <section className="panel">
          <table>
            <thead>
              <tr>
                <th>创建时间</th>
                <th>场景</th>
                <th>状态</th>
                <th>群</th>
                <th>内容</th>
                <th>outbox_id</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((outbox) => (
                <tr
                  className={outbox.outbox_id === selectedOutboxId ? "selected-row" : ""}
                  key={outbox.outbox_id}
                  onClick={() => setSelectedOutboxId(outbox.outbox_id)}
                >
                  <td>{compactTime(outbox.created_at)}</td>
                  <td>
                    <StatusBadge status={outbox.scene} />
                  </td>
                  <td>
                    <StatusBadge status={outbox.status} />
                  </td>
                  <td>{outbox.chatid}</td>
                  <td className="message-cell">{outboxContentText(outbox)}</td>
                  <td className="mono">{outbox.outbox_id}</td>
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
        <OutboxDetail
          outbox={selected}
          sending={sendMutation.isPending}
          onSend={(outboxId) => sendMutation.mutate(outboxId)}
        />
      </div>
    </section>
  );
}

function OutboxDetail({
  outbox,
  sending,
  onSend
}: {
  outbox?: OutboxItem;
  sending: boolean;
  onSend: (outboxId: string) => void;
}) {
  if (!outbox) {
    return (
      <section className="panel detail-panel">
        <div className="panel-title">发送详情</div>
        <p className="muted detail-empty">选择一条记录查看详情。</p>
      </section>
    );
  }
  const canSend = outbox.status === "pending" || outbox.status === "failed";
  return (
    <section className="panel detail-panel">
      <div className="panel-title detail-title-row">
        <span>发送详情</span>
        <button
          type="button"
          disabled={!canSend || sending}
          onClick={() => {
            if (window.confirm(`确认发送 ${outbox.outbox_id} 吗？`)) {
              onSend(outbox.outbox_id);
            }
          }}
        >
          {sending ? "发送中" : "手动发送"}
        </button>
      </div>
      <div className="kv-list">
        <span>outbox_id</span>
        <strong className="mono">{outbox.outbox_id}</strong>
        <span>场景</span>
        <StatusBadge status={outbox.scene} />
        <span>状态</span>
        <StatusBadge status={outbox.status} />
        <span>企微 msgid</span>
        <strong className="mono">{outbox.external_msgid ?? "-"}</strong>
        <span>重试</span>
        <strong>{outbox.retry_count}</strong>
      </div>
      {outbox.error_message ? <div className="inline-error">{outbox.error_message}</div> : null}
      <div className="panel-title">内容 JSON</div>
      <JsonBlock value={outbox.content ?? {}} />
      <div className="panel-title">发送响应</div>
      <JsonBlock value={outbox.raw_response ?? {}} />
    </section>
  );
}

function outboxContentText(outbox: OutboxItem) {
  const typedContent = outbox.content?.[outbox.msgtype];
  if (typeof typedContent === "object" && typedContent !== null && "content" in typedContent) {
    return String((typedContent as { content?: unknown }).content ?? "");
  }
  if (typeof typedContent === "string") {
    return typedContent;
  }
  return JSON.stringify(outbox.content ?? {});
}
