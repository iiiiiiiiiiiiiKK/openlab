import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, openEventSocket } from "@/lib/api";
import type { EventPayload, HostReport, TaskRow } from "@openlab/types";

interface SystemState {
  host: HostReport | null;
  tasks: TaskRow[];
  events: EventPayload[];
  loading: boolean;
  refreshHost: () => Promise<void>;
  refreshTasks: () => Promise<void>;
  subscribe: (listener: (event: EventPayload) => void) => () => void;
}

const SystemContext = createContext<SystemState | null>(null);

export function SystemProvider({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HostReport | null>(null);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [events, setEvents] = useState<EventPayload[]>([]);
  const [loading, setLoading] = useState(true);
  const listeners = useMemo(() => new Set<(event: EventPayload) => void>(), []);

  const refreshHost = useCallback(async () => {
    try {
      setHost(await api.host());
    } catch {
      setHost(null);
    }
  }, []);

  const refreshTasks = useCallback(async () => {
    try {
      const payload = await api.listTasks();
      setTasks(payload.items);
    } catch {
      /* backend may still be starting */
    }
  }, []);

  const subscribe = useCallback(
    (listener: (event: EventPayload) => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    [listeners],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await Promise.all([refreshHost(), refreshTasks()]);
      if (!cancelled) setLoading(false);
    })();
    const interval = window.setInterval(() => {
      void refreshHost();
      void refreshTasks();
    }, 20_000);
    const stop = openEventSocket((event) => {
      setEvents((current) => [...current.slice(-199), event]);
      listeners.forEach((listener) => listener(event));
      const type = event.type;
      if (type === "task.queued" || type === "task.started" || type === "task.finished" || type === "task.failed") {
        void refreshTasks();
      }
    });
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      stop();
    };
  }, [listeners, refreshHost, refreshTasks]);

  const value = useMemo<SystemState>(
    () => ({ host, tasks, events, loading, refreshHost, refreshTasks, subscribe }),
    [host, tasks, events, loading, refreshHost, refreshTasks, subscribe],
  );

  return <SystemContext.Provider value={value}>{children}</SystemContext.Provider>;
}

export function useSystem(): SystemState {
  const context = useContext(SystemContext);
  if (!context) throw new Error("useSystem must be used inside SystemProvider");
  return context;
}
