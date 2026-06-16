import { FormEvent, useState } from "react";
import { LockKeyhole, LogIn } from "lucide-react";

import { ApiError, api } from "../api/client";
import type { AdminSession } from "../api/types";

type Props = {
  onLogin: (session: AdminSession) => void;
};

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const session = await api.adminLogin({ username, password });
      onLogin(session);
    } catch (exc) {
      if (exc instanceof ApiError) {
        setError(exc.message);
      } else {
        setError("登录失败，请稍后再试");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="login-brand">
          <LockKeyhole size={28} />
          <div>
            <h1>企微机器人管理台</h1>
            <p>管理员登录</p>
          </div>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            账号
            <input
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            密码
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error ? <div className="inline-error">{error}</div> : null}
          <button className="primary-button" type="submit" disabled={submitting}>
            <LogIn size={17} />
            <span>{submitting ? "登录中" : "登录"}</span>
          </button>
        </form>
      </section>
    </main>
  );
}
