import { ApiError } from "../api/client";

type Props = {
  error: unknown;
};

export function ErrorPanel({ error }: Props) {
  if (!error) {
    return null;
  }
  if (error instanceof ApiError) {
    return (
      <div className="error-panel">
        <strong>{error.code}</strong>
        <span>{error.message}</span>
      </div>
    );
  }
  return <div className="error-panel">{String(error)}</div>;
}
