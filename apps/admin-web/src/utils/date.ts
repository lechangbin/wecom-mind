export function today(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function compactTime(value?: string | null): string {
  if (!value) {
    return "";
  }
  return value.replace("T", " ").replace(/\.\d+/, "").replace(/\+00:00|Z/, "");
}
