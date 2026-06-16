import { useEffect, useRef } from "react";
import { ApiError } from "../api/client";

type Props = {
  error: unknown;
};

const DATE_RANGE_MESSAGES = new Set([
  "最多只能查询一个月内的数据",
  "开始日期不能晚于截止日期"
]);

export function ErrorPanel({ error }: Props) {
  const lastAlertError = useRef<unknown>(null);
  const alertMessage = dateRangeAlertMessage(error);

  useEffect(() => {
    if (!alertMessage) {
      lastAlertError.current = null;
      return;
    }
    if (lastAlertError.current === error) {
      return;
    }
    lastAlertError.current = error;
    window.alert(alertMessage);
  }, [alertMessage, error]);

  if (!error || alertMessage) {
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

function dateRangeAlertMessage(error: unknown): string | null {
  if (
    error instanceof ApiError &&
    error.code === "INVALID_ARGUMENT" &&
    DATE_RANGE_MESSAGES.has(error.message)
  ) {
    return error.message;
  }
  return null;
}
