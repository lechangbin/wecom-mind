import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCcw, UserRoundCog } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { UserProfile } from "../api/types";
import { ErrorPanel } from "../components/ErrorPanel";
import { JsonBlock } from "../components/JsonBlock";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { compactTime } from "../utils/date";

const LIMIT = 25;

export function ProfilesPage() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [selectedUserid, setSelectedUserid] = useState<string | null>(null);
  const [generationNotice, setGenerationNotice] = useState("");
  const users = useQuery({
    queryKey: ["users", offset],
    queryFn: () => api.users({ limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    staleTime: 30_000
  });
  const profile = useQuery({
    queryKey: ["userProfile", selectedUserid],
    queryFn: () => api.userProfile(selectedUserid as string),
    enabled: Boolean(selectedUserid),
    staleTime: 60_000
  });
  const versions = useQuery({
    queryKey: ["userProfileVersions", selectedUserid],
    queryFn: () => api.userProfileVersions(selectedUserid as string, { limit: 10, offset: 0 }),
    enabled: Boolean(selectedUserid),
    staleTime: 60_000
  });
  const generateProfile = useMutation({
    mutationFn: (force: boolean) => api.generateUserProfile(selectedUserid as string, { force }),
    onSuccess: (result) => {
      if (result.force && result.profile_update_count > 0) {
        setGenerationNotice(
          `已基于 ${result.conversation_count} 条会话强制重写画像，写入 ${result.profile_update_count} 次画像`
        );
      } else if (result.force) {
        setGenerationNotice(
          `已检查 ${result.conversation_count} 条会话，Dify 未生成可重写画像，保留原画像`
        );
      } else if (result.profile_update_count === 0) {
        setGenerationNotice(`已检查 ${result.conversation_count} 条会话，Dify 判定无新增画像`);
      } else {
        setGenerationNotice(`已检查 ${result.conversation_count} 条会话，更新 ${result.profile_update_count} 次画像`);
      }
      void queryClient.invalidateQueries({ queryKey: ["users"] });
      void queryClient.invalidateQueries({ queryKey: ["userProfile", selectedUserid] });
      void queryClient.invalidateQueries({ queryKey: ["userProfileVersions", selectedUserid] });
    }
  });
  const runProfileGeneration = (force: boolean) => {
    setGenerationNotice("");
    if (!selectedUserid) {
      setGenerationNotice("请先选择一个用户。");
      return;
    }
    generateProfile.mutate(force);
  };
  useEffect(() => {
    const firstUserid = users.data?.items[0]?.userid;
    if (!selectedUserid && firstUserid) {
      setSelectedUserid(firstUserid);
    }
  }, [selectedUserid, users.data?.items]);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <h1>用户画像</h1>
          <p>查看当前画像、证据事实和版本历史。</p>
        </div>
        <div className="header-actions">
          <button
            className="primary-button"
            type="button"
            disabled={generateProfile.isPending || users.isLoading || (users.data?.items.length ?? 0) === 0}
            onClick={() => runProfileGeneration(false)}
          >
            <UserRoundCog size={16} />
            {generateProfile.isPending ? "生成中" : "生成画像"}
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={generateProfile.isPending || users.isLoading || (users.data?.items.length ?? 0) === 0}
            onClick={() => runProfileGeneration(true)}
          >
            <RefreshCcw size={16} />
            强制重写
          </button>
        </div>
      </header>
      <ErrorPanel error={users.error || profile.error || versions.error || generateProfile.error} />
      {generationNotice ? <div className="notice-panel">{generationNotice}</div> : null}
      <div className="content-split">
        <section className="panel">
          <table>
            <thead>
              <tr>
                <th>用户</th>
                <th>状态</th>
                <th>消息数</th>
                <th>最近活跃</th>
                <th>画像摘要</th>
              </tr>
            </thead>
            <tbody>
              {(users.data?.items ?? []).map((user) => (
                <tr
                  className={user.userid === selectedUserid ? "selected-row" : ""}
                  key={user.userid}
                  onClick={() => setSelectedUserid(user.userid)}
                >
                  <td>
                    <strong>{user.name || user.userid}</strong>
                    <div className="mono">{user.userid}</div>
                  </td>
                  <td>
                    <StatusBadge status={user.status} />
                  </td>
                  <td>{user.message_count}</td>
                  <td>{compactTime(user.last_active_at)}</td>
                  <td className="message-cell">{user.latest_profile_summary ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination
            total={users.data?.total ?? 0}
            limit={LIMIT}
            offset={offset}
            onOffsetChange={setOffset}
          />
        </section>
        <ProfileDetail profile={profile.data} versions={versions.data?.items ?? []} />
      </div>
    </section>
  );
}

function ProfileDetail({
  profile,
  versions
}: {
  profile?: UserProfile;
  versions: UserProfile[];
}) {
  if (!profile) {
    return (
      <section className="panel detail-panel">
        <div className="panel-title">画像详情</div>
        <p className="muted detail-empty">选择用户查看画像。</p>
      </section>
    );
  }
  return (
    <section className="panel detail-panel">
      <div className="panel-title">画像详情</div>
      <div className="kv-list">
        <span>userid</span>
        <strong className="mono">{profile.userid}</strong>
        <span>版本</span>
        <strong>v{profile.version}</strong>
        <span>状态</span>
        <StatusBadge status={profile.status} />
        <span>置信度</span>
        <strong>{profile.confidence}</strong>
      </div>
      <div className="profile-summary">{profile.summary || "暂无摘要"}</div>
      <div className="panel-title">画像事实</div>
      <div className="list-stack fact-list">
        {profile.facts.length === 0 ? <p className="muted detail-empty">暂无事实。</p> : null}
        {profile.facts.map((fact) => (
          <article className="fact-card" key={fact.id}>
            <div>
              <strong>{fact.label}</strong>
              <StatusBadge status={fact.status} />
            </div>
            <p>{fact.description}</p>
            <div className="meta-row">
              <span>{fact.fact_type}</span>
              <span>confidence {fact.confidence}</span>
              <span>msgids {fact.evidence_msgids.length}</span>
              <span>conversations {fact.evidence_conversation_nos.length}</span>
            </div>
          </article>
        ))}
      </div>
      <div className="panel-title">版本历史</div>
      <div className="version-list">
        {versions.map((item) => (
          <div className="version-row" key={item.id}>
            <span>v{item.version}</span>
            <StatusBadge status={item.status} />
            <span>{compactTime(item.created_at)}</span>
            <span>{item.summary || "-"}</span>
          </div>
        ))}
      </div>
      <div className="panel-title">画像 JSON</div>
      <JsonBlock value={profile.profile_json ?? {}} />
    </section>
  );
}
