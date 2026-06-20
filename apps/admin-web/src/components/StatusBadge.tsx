type Props = {
  status: string;
};

const labels: Record<string, string> = {
  replying: "正在回复",
  replied: "已回复",
  error: "异常",
  pending: "待处理",
  running: "运行中",
  success: "成功",
  failed: "失败",
  invalid_output: "输出异常",
  sent: "已发送",
  sending: "发送中",
  canceled: "已取消",
  active: "有效",
  summary: "摘要命中",
  message: "消息命中",
  source_duplicate: "来源重复",
  none: "未命中",
  unknown: "未观测",
  disabled: "未启用",
  reply: "@ 回复",
  reply_recovery: "@ 恢复",
  proactive: "主动回复"
};

export function StatusBadge({ status }: Props) {
  return <span className={`status status-${status}`}>{labels[status] ?? status}</span>;
}
