import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  Bot,
  FileText,
  FolderTree,
  History,
  Image as ImageIcon,
  Play,
  ScrollText,
  Square,
  Terminal,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectDetail, TaskRow } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { useToast } from "@/components/ui/toast";
import { useLocalState } from "@/lib/hooks";
import { cn, truncate } from "@/lib/utils";
import { useT } from "@/lib/i18n";
import { PreviewPanel } from "@/features/workspace/PreviewPanel";
import { TerminalPanel } from "@/features/workspace/TerminalPanel";
import { LogsPanel } from "@/features/workspace/LogsPanel";
import { ExplorerPanel } from "@/features/workspace/ExplorerPanel";
import { AgentPanel } from "@/features/workspace/AgentPanel";
import { AssetsPanel } from "@/features/workspace/AssetsPanel";
import { ReportPanel } from "@/features/workspace/ReportPanel";
import { SnapshotPanel } from "@/features/workspace/SnapshotPanel";

type TabId = "terminal" | "logs" | "explorer" | "agent" | "assets" | "report" | "snapshots";

const TABS: Array<{ id: TabId; labelKey: string; icon: typeof Terminal }> = [
  { id: "terminal", labelKey: "workspace.tabTerminal", icon: Terminal },
  { id: "logs", labelKey: "workspace.tabLogs", icon: ScrollText },
  { id: "explorer", labelKey: "workspace.tabExplorer", icon: FolderTree },
  { id: "agent", labelKey: "workspace.tabAgent", icon: Bot },
  { id: "assets", labelKey: "workspace.tabAssets", icon: ImageIcon },
  { id: "report", labelKey: "workspace.tabReport", icon: FileText },
  { id: "snapshots", labelKey: "workspace.tabSnapshots", icon: History },
];

export function ProjectWorkspacePage() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [tab, setTab] = useState<TabId>("logs");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [topHeight, setTopHeight] = useLocalState<number>("openlab.workspace.top", 58);
  const dragging = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const t = useT();

  const load = useCallback(async () => {
    try {
      const detail = await api.getProject(projectId);
      setProject(detail);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
    try {
      const payload = await api.listTasks({ project_id: projectId });
      setTasks(payload.items);
    } catch {
      /* ignore */
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 6000);
    return () => window.clearInterval(interval);
  }, [load]);

  useEffect(() => {
    const move = (event: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const percent = ((event.clientY - rect.top) / rect.height) * 100;
      setTopHeight(Math.max(28, Math.min(78, percent)));
    };
    const up = () => {
      dragging.current = false;
      document.body.style.cursor = "";
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [setTopHeight]);

  const liveUrl = useMemo(() => {
    if (!project) return null;
    const container = project.containers.find((item) => item.host_port);
    return container?.host_port ? `http://127.0.0.1:${container.host_port}` : null;
  }, [project]);

  const activeTask = tasks.find((task) => task.status === "running") ?? tasks[0];
  const isRunning = project?.deploy_status === "RUNNING";

  const deploy = async () => {
    setBusy(true);
    try {
      const result = await api.deploy(projectId);
      toast.success(t("projects.deployQueued"), `task ${result.task_id.slice(0, 8)}`);
      await load();
      setTab("logs");
    } catch (exc) {
      toast.error(t("error.deployStart"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await api.stop(projectId);
      toast.success(t("status.STOPPED"));
      await load();
    } catch (exc) {
      toast.error(t("error.stopProject"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  if (error && !project) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-ink">{t("workspace.unavailable")}</p>
          <p className="mt-1 text-xs text-danger">{error}</p>
          <Link to="/projects" className="mt-3 inline-block text-xs text-[#9dbcff] hover:underline">
            {t("workspace.back")}
          </Link>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-xs text-faint">{t("workspace.loading")}</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-hairline bg-surface px-3 py-2">
        <Link to="/projects" className="rounded p-1 text-faint transition hover:bg-white/5 hover:text-ink">
          <ArrowLeft className="h-3.5 w-3.5" />
        </Link>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-ink">{project.display_name || project.name}</p>
            <StatusBadge status={project.deploy_status} />
            {project.environment?.framework ? <Badge>{project.environment.framework}</Badge> : null}
            {project.environment?.gpu_required ? <Badge tone="warn">GPU</Badge> : null}
          </div>
          <p className="truncate text-2xs text-faint">
            {project.source_url || t("import.tabLocal")} · {project.plan?.strategy ?? t("workspace.unknownPlan")} ·{" "}
            {project.plan?.confidence !== undefined ? t("workspace.confidence", { percent: Math.round((project.plan.confidence ?? 0) * 100) }) : "—"}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          {activeTask && (activeTask.status === "running" || activeTask.status === "queued") ? (
            <div className="mr-2 hidden w-[180px] items-center gap-2 sm:flex">
              <div className="min-w-0 flex-1">
                <p className="truncate text-2xs text-muted">{truncate(activeTask.message || activeTask.stage, 40)}</p>
                <Progress value={activeTask.progress} className="mt-1" />
              </div>
              <Button
                size="icon"
                variant="ghost"
                title={t("workspace.cancelTask")}
                onClick={() => void api.taskAction(activeTask.id, "cancel").then(load)}
              >
                <Square className="h-3 w-3" />
              </Button>
            </div>
          ) : null}
          {liveUrl ? (
            <a
              href={liveUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 rounded border border-hairline-strong px-2 py-1 text-2xs text-muted transition hover:text-ink"
            >
              <Activity className="h-3 w-3 text-ok" />
              {liveUrl.replace("http://", "")}
            </a>
          ) : null}
          <Button variant="secondary" size="sm" loading={busy} onClick={() => void deploy()}>
            <Play className="h-3.5 w-3.5" />
            {isRunning ? t("workspace.rebuild") : t("workspace.deploy")}
          </Button>
          {isRunning ? (
            <Button variant="secondary" size="sm" loading={busy} onClick={() => void stop()}>
              <Square className="h-3 w-3" />
              Stop
            </Button>
          ) : null}
        </div>
      </div>

      {project.deploy_status_detail ? (
        <div
          className={cn(
            "border-b px-3 py-1.5 text-2xs",
            project.deploy_status === "WAITING_FOR_USER" || project.deploy_status === "FAILED"
              ? "border-danger/30 bg-danger/[0.07] text-danger"
              : "border-hairline bg-canvas text-faint",
          )}
        >
          {project.deploy_status_detail}
        </div>
      ) : null}

      <div ref={containerRef} className="flex min-h-0 flex-1 flex-col">
        <div style={{ height: `${topHeight}%` }} className="min-h-0 border-b border-hairline">
          <PreviewPanel projectId={project.id} url={liveUrl} onCaptured={() => void load()} />
        </div>

        <div
          onMouseDown={() => {
            dragging.current = true;
            document.body.style.cursor = "row-resize";
          }}
          className="group flex h-[7px] cursor-row-resize items-center justify-center bg-canvas"
          role="separator"
          aria-orientation="horizontal"
        >
          <div className="h-[2px] w-10 rounded-full bg-hairline-strong transition group-hover:bg-accent/60" />
        </div>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-0.5 border-b border-hairline bg-surface px-2">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className={cn(
                  "relative flex items-center gap-1.5 px-2.5 py-2 text-2xs transition",
                  tab === item.id ? "text-ink" : "text-faint hover:text-muted",
                )}
              >
                <item.icon className="h-3 w-3" />
                {t(item.labelKey)}
                {tab === item.id ? <span className="absolute inset-x-1 -bottom-px h-[2px] rounded-t bg-accent" /> : null}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1">
            {tab === "terminal" ? (
              <TerminalPanel projectId={project.id} canExec={Boolean(liveUrl)} />
            ) : tab === "logs" ? (
              <LogsPanel projectId={project.id} />
            ) : tab === "explorer" ? (
              <ExplorerPanel projectId={project.id} />
            ) : tab === "agent" ? (
              <AgentPanel project={project} onRefresh={() => void load()} />
            ) : tab === "assets" ? (
              <AssetsPanel projectId={project.id} canCapture={Boolean(liveUrl)} onCaptured={() => void load()} />
            ) : tab === "snapshots" ? (
              <SnapshotPanel projectId={project.id} />
            ) : (
              <ReportPanel projectId={project.id} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
