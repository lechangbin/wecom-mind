import {
  Activity,
  BotMessageSquare,
  BrainCircuit,
  LogOut,
  FlaskConical,
  LayoutDashboard,
  type LucideIcon,
  MessagesSquare,
  UserRoundCog,
  Send,
  SplitSquareVertical
} from "lucide-react";
import type React from "react";

export type PageKey =
  | "dashboard"
  | "replyTasks"
  | "messages"
  | "aiRuns"
  | "outbox"
  | "conversations"
  | "profiles"
  | "liveTest";

type Props = {
  activePage: PageKey;
  onPageChange: (page: PageKey) => void;
  username: string | null;
  onLogout: () => void;
  children: React.ReactNode;
};

const navItems: Array<{
  key: PageKey;
  label: string;
  icon: LucideIcon;
}> = [
  { key: "dashboard", label: "总览", icon: LayoutDashboard },
  { key: "replyTasks", label: "响应消息", icon: BotMessageSquare },
  { key: "messages", label: "完整消息", icon: MessagesSquare },
  { key: "aiRuns", label: "AI 运行", icon: BrainCircuit },
  { key: "outbox", label: "发送记录", icon: Send },
  { key: "conversations", label: "会话审查", icon: SplitSquareVertical },
  { key: "profiles", label: "用户画像", icon: UserRoundCog },
  { key: "liveTest", label: "实机测试", icon: FlaskConical }
];

export function Layout({ activePage, onPageChange, username, onLogout, children }: Props) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Activity size={22} />
          <div>
            <strong>企微机器人</strong>
            <span>管理台</span>
          </div>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={item.key === activePage ? "nav-item active" : "nav-item"}
                key={item.key}
                type="button"
                onClick={() => onPageChange(item.key)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-account">
          <span>{username ?? "管理员"}</span>
          <button type="button" onClick={onLogout} title="退出登录">
            <LogOut size={16} />
            <span>退出</span>
          </button>
        </div>
      </aside>
      <main className="main-panel">{children}</main>
    </div>
  );
}
