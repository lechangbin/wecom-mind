import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { adminEventUrl } from "../api/client";
import type { AdminEvent, Page, ReplyTask } from "../api/types";

export function useAdminEvents() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const source = new EventSource(adminEventUrl());
    source.onmessage = (event) => {
      const adminEvent = JSON.parse(event.data) as AdminEvent;
      if (adminEvent.event_type !== "reply_task_updated") {
        return;
      }
      const task = adminEvent.payload as ReplyTask;
      let patched = false;
      queryClient.setQueriesData<Page<ReplyTask>>({ queryKey: ["replyTasks"] }, (old) => {
        if (!old) {
          return old;
        }
        const index = old.items.findIndex((item) => item.task_id === task.task_id);
        if (index === -1) {
          return old;
        }
        patched = true;
        const nextItems = [...old.items];
        nextItems[index] = task;
        return { ...old, items: nextItems };
      });
      if (!patched) {
        void queryClient.invalidateQueries({
          queryKey: ["replyTasks"],
          refetchType: "active"
        });
      }
    };
    return () => source.close();
  }, [queryClient]);
}
