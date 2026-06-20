import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./api/client";
import type { AdminSession } from "./api/types";
import { Layout, type PageKey } from "./components/Layout";
import { useAdminEvents } from "./hooks/useAdminEvents";
import { AiRunsPage } from "./pages/AiRunsPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LiveTestPage } from "./pages/LiveTestPage";
import { LoginPage } from "./pages/LoginPage";
import { MessagesPage } from "./pages/MessagesPage";
import { OutboxPage } from "./pages/OutboxPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { ReplyTasksPage } from "./pages/ReplyTasksPage";

export function App() {
  const [activePage, setActivePage] = useState<PageKey>("replyTasks");
  const [session, setSession] = useState<AdminSession | null>(null);
  const [authStatus, setAuthStatus] = useState<"checking" | "authenticated" | "anonymous">(
    "checking"
  );
  const queryClient = useQueryClient();
  useAdminEvents(authStatus === "authenticated");

  useEffect(() => {
    let active = true;
    api
      .adminMe()
      .then((nextSession) => {
        if (!active) {
          return;
        }
        setSession(nextSession);
        setAuthStatus(nextSession.authenticated ? "authenticated" : "anonymous");
      })
      .catch((exc) => {
        if (!active) {
          return;
        }
        if (exc instanceof ApiError && exc.code === "UNAUTHORIZED") {
          setAuthStatus("anonymous");
          return;
        }
        setAuthStatus("anonymous");
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleLogout() {
    await api.adminLogout();
    queryClient.clear();
    setSession(null);
    setAuthStatus("anonymous");
  }

  if (authStatus === "checking") {
    return (
      <main className="login-shell">
        <section className="login-panel">
          <div className="login-brand">
            <strong>企微机器人管理台</strong>
            <p>正在检查登录状态</p>
          </div>
        </section>
      </main>
    );
  }

  if (authStatus !== "authenticated") {
    return (
      <LoginPage
        onLogin={(nextSession) => {
          setSession(nextSession);
          setAuthStatus("authenticated");
        }}
      />
    );
  }

  return (
    <Layout
      activePage={activePage}
      onPageChange={setActivePage}
      username={session?.username ?? null}
      onLogout={() => {
        void handleLogout();
      }}
    >
      {activePage === "dashboard" ? <DashboardPage /> : null}
      {activePage === "replyTasks" ? <ReplyTasksPage /> : null}
      {activePage === "messages" ? <MessagesPage /> : null}
      {activePage === "aiRuns" ? <AiRunsPage /> : null}
      {activePage === "outbox" ? <OutboxPage /> : null}
      {activePage === "conversations" ? <ConversationsPage /> : null}
      {activePage === "profiles" ? <ProfilesPage /> : null}
      {activePage === "liveTest" ? <LiveTestPage /> : null}
    </Layout>
  );
}
