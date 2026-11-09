/** Typed API client. All calls hit the local backend on 127.0.0.1:3210. */

import type {
  AppSettings,
  AssetRow,
  EventPayload,
  ExplorationPlanRow,
  HostReport,
  OverviewPayload,
  ProjectDetail,
  ProjectSummary,
  ProviderRow,
  ReportRow,
  TaskLogRow,
  TaskRow,
  UsageSummary,
} from "@openlab/types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") detail = payload.detail;
      else if (payload?.detail) detail = JSON.stringify(payload.detail);
    } catch {
      /* keep the status text */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });

export const api = {
  health: () => request<{ ok: boolean; version: string; data_dir: string }>("/health"),
  host: () => request<HostReport>("/system/host"),
  overview: () => request<OverviewPayload>("/overview"),
  recentEvents: (limit = 40) =>
    request<{ events: EventPayload[] }>(`/system/events?limit=${limit}`),

  listProjects: (params?: { q?: string; status?: string; sort?: string }) => {
    const search = new URLSearchParams();
    if (params?.q) search.set("q", params.q);
    if (params?.status) search.set("status", params.status);
    if (params?.sort) search.set("sort", params.sort);
    const qs = search.toString();
    return request<{ items: ProjectSummary[]; total: number }>(`/projects${qs ? `?${qs}` : ""}`);
  },
  getProject: (id: string) => request<ProjectDetail>(`/projects/${id}`),
  updateProject: (id: string, body: Partial<{ display_name: string; favorite: boolean; tags: string[]; description: string }>) =>
    patch<ProjectSummary>(`/projects/${id}`, body),
  importGithub: (url: string, token?: string) =>
    post<{ project_id: string; name: string; files: number; analysis: Record<string, unknown> }>(
      "/projects/import/github",
      { url, token: token || undefined },
    ),
  importZip: async (file: File, displayName?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (displayName) form.append("display_name", displayName);
    return request<{ project_id: string; name: string; files: number }>("/projects/import/zip", {
      method: "POST",
      body: form,
    });
  },
  importLocal: (path: string, displayName?: string) =>
    post<{ project_id: string; name: string; files: number }>("/projects/import/local", {
      path,
      display_name: displayName,
    }),
  deploy: (id: string) => post<{ task_id: string }>(`/projects/${id}/deploy`, { rebuild: true }),
  stop: (id: string) => post<{ stopped: string[] }>(`/projects/${id}/stop`),
  reanalyze: (id: string) => post<{ analysis: Record<string, unknown> }>(`/projects/${id}/analyze`),
  resetWorkspace: (id: string) => post<{ restored_files: number }>(`/projects/${id}/reset-workspace`),
  deleteProject: (id: string) => del<{ deleted: boolean }>(`/projects/${id}`),
  projectTree: (id: string, path = "", depth = 2) =>
    request<{ entries: TreeEntry[] }>(`/projects/${id}/tree?path=${encodeURIComponent(path)}&depth=${depth}`),
  projectFile: (id: string, path: string) =>
    request<{ path: string; content: string; size: number }>(`/projects/${id}/file?path=${encodeURIComponent(path)}`),
  projectLogs: (id: string, name = "deploy", tail = 400) =>
    request<{ name: string; lines: string[]; exists: boolean }>(`/projects/${id}/logs?name=${name}&tail=${tail}`),
  terminalHistory: (id: string) =>
    request<{ lines: TaskLogRow[] }>(`/projects/${id}/terminal/history`),
  terminalExec: (id: string, command: string) =>
    post<{ ok: boolean; code: number; stdout: string; stderr: string; duration_ms: number }>(
      `/projects/${id}/terminal/exec?command=${encodeURIComponent(command)}`,
    ),
  patches: (id: string) =>
    request<{
      items: Array<{
        id: string;
        attempt: number;
        diagnosis: string | null;
        files_touched: string[];
        applied: boolean;
        rolled_back: boolean;
        diff: string | null;
        created_at: string | null;
      }>;
    }>(`/projects/${id}/patches`),
  snapshots: (id: string) =>
    request<{
      items: Array<{
        id: string;
        label: string | null;
        compose_path: string | null;
        env_names: string[];
        created_at: string | null;
      }>;
    }>(`/projects/${id}/snapshots`),
  createSnapshot: (id: string, label?: string) =>
    post<{ id: string; label: string }>(`/projects/${id}/snapshots`, { label }),
  restoreSnapshot: (id: string, snapshotId: string) =>
    post<{ restored: boolean; label: string | null; compose_path: string | null; next_step: string }>(
      `/projects/${id}/snapshots/${snapshotId}/restore`,
    ),

  preview: (id: string, body: { viewport?: string; full_page?: boolean; url?: string }) =>
    post<{
      url: string;
      title: string;
      snapshot: Record<string, unknown> & { asset_id?: string; asset_path?: string };
      screenshot: { path: string; width: number; height: number } | null;
      console: Record<string, unknown>;
    }>(`/projects/${id}/preview`, body),
  explorePlan: (id: string, body: { mode: string; goal?: string; url?: string }) =>
    post<{ plan_id: string; plan: Record<string, unknown> }>(`/projects/${id}/explore/plan`, body),
  explore: (id: string, body: { mode: string; goal?: string; record_video?: boolean; url?: string }) =>
    post<{ task_id: string }>(`/projects/${id}/explore`, body),
  explorePlans: (id: string) => request<{ items: ExplorationPlanRow[] }>(`/projects/${id}/explore/plans`),
  explorePlanById: (id: string, planId: string) =>
    request<ExplorationPlanRow>(`/projects/${id}/explore/plans/${planId}`),
  browserSessions: (id: string) =>
    request<{ items: Array<{ id: string; name: string; url: string | null; is_authenticated: boolean }> }>(
      `/projects/${id}/browser-sessions`,
    ),
  openBrowserSession: (id: string, body: { name?: string; url?: string; wait_seconds?: number }) =>
    post<{ session_id: string; storage_path: string; url: string }>(`/projects/${id}/browser-sessions`, body),

  listAssets: (params?: { project_id?: string; kind?: string }) => {
    const search = new URLSearchParams();
    if (params?.project_id) search.set("project_id", params.project_id);
    if (params?.kind) search.set("kind", params.kind);
    const qs = search.toString();
    return request<{ items: AssetRow[]; stats: { total: number; bytes: number; by_kind: Record<string, { count: number; bytes: number }> } }>(
      `/assets${qs ? `?${qs}` : ""}`,
    );
  },
  renameAsset: (id: string, label: string) => patch<AssetRow>(`/assets/${id}`, { label }),
  deleteAsset: (id: string) => del<{ deleted: boolean }>(`/assets/${id}`),
  makeGif: (body: { asset_id: string; start_s?: number; duration_s?: number; fps?: number; width?: number }) =>
    post<AssetRow>("/assets/gif", body),
  convertVideo: (body: { asset_id: string; format: "mp4" | "webm"; max_width?: number }) =>
    post<AssetRow>("/assets/video/convert", body),
  captureAssets: (projectId: string) => post<{ task_id: string }>(`/assets/capture/${projectId}`),
  exportAssets: (assetIds: string[], targetDir: string) =>
    post<{ exported: string[]; count: number }>("/assets/export", {
      asset_ids: assetIds,
      target_dir: targetDir,
    }),

  listReports: (projectId?: string) =>
    request<{ items: ReportRow[] }>(`/reports${projectId ? `?project_id=${projectId}` : ""}`),
  generateReport: (projectId: string) => post<ReportRow>(`/projects/${projectId}/reports`),
  getReport: (id: string) => request<ReportRow>(`/reports/${id}`),
  reportMarkdown: async (id: string) => {
    const response = await fetch(`${BASE}/reports/${id}/markdown`);
    if (!response.ok) throw new ApiError(response.status, "report markdown unavailable");
    return response.text();
  },

  listProviders: () =>
    request<{ items: ProviderRow[]; defaults: Record<string, Record<string, string>> }>("/providers"),
  createProvider: (body: Record<string, unknown>) => post<ProviderRow>("/providers", body),
  updateProvider: (id: string, body: Record<string, unknown>) => patch<ProviderRow>(`/providers/${id}`, body),
  deleteProvider: (id: string) => del<{ deleted: boolean }>(`/providers/${id}`),
  testProvider: (id: string) =>
    post<{ ok: boolean; model?: string; latency_ms?: number; reply?: string; error?: string }>(
      `/providers/${id}/test`,
    ),
  usageSummary: (params?: { project_id?: string; days?: number }) => {
    const search = new URLSearchParams();
    if (params?.project_id) search.set("project_id", params.project_id);
    if (params?.days) search.set("days", String(params.days));
    const qs = search.toString();
    return request<UsageSummary>(`/providers/usage/summary${qs ? `?${qs}` : ""}`);
  },

  getSettings: () => request<AppSettings>("/settings"),
  updateSettings: (body: Record<string, unknown>) => patch<AppSettings>("/settings", body),

  listTasks: (params?: { project_id?: string; status?: string }) => {
    const search = new URLSearchParams();
    if (params?.project_id) search.set("project_id", params.project_id);
    if (params?.status) search.set("status", params.status);
    const qs = search.toString();
    return request<{ items: TaskRow[] }>(`/tasks${qs ? `?${qs}` : ""}`);
  },
  getTask: (id: string) => request<TaskRow & { logs: TaskLogRow[] }>(`/tasks/${id}`),
  taskAction: (id: string, action: "cancel" | "pause" | "resume" | "retry") =>
    post<{ ok: boolean; new_task_id?: string }>(`/tasks/${id}/action`, { action }),
};

export interface TreeEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size: number;
  modified: string;
  children?: TreeEntry[];
}

export function assetRawUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/raw`;
}

export function openEventSocket(onEvent: (event: EventPayload) => void): () => void {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${window.location.host}/ws/events`;
  let socket: WebSocket | null = null;
  let closed = false;
  let retry = 0;
  let timer: number | undefined;

  const connect = () => {
    if (closed) return;
    socket = new WebSocket(url);
    socket.onopen = () => {
      retry = 0;
    };
    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as EventPayload;
        if (parsed.type !== "ping") onEvent(parsed);
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onclose = () => {
      if (closed) return;
      retry = Math.min(retry + 1, 6);
      timer = window.setTimeout(connect, 500 * 2 ** retry);
    };
    socket.onerror = () => socket?.close();
  };
  connect();

  return () => {
    closed = true;
    if (timer) window.clearTimeout(timer);
    socket?.close();
  };
}
