import { useState } from "react";
import { Layout, type PageKey } from "./components/Layout";
import { useAdminEvents } from "./hooks/useAdminEvents";
import { AiRunsPage } from "./pages/AiRunsPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LiveTestPage } from "./pages/LiveTestPage";
import { MessagesPage } from "./pages/MessagesPage";
import { OutboxPage } from "./pages/OutboxPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { ReplyTasksPage } from "./pages/ReplyTasksPage";

export function App() {
  const [activePage, setActivePage] = useState<PageKey>("replyTasks");
  useAdminEvents();

  return (
    <Layout activePage={activePage} onPageChange={setActivePage}>
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
