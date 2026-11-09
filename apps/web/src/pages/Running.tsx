import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, ExternalLink, Pause, Play, RefreshCw, Square, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectSummary, TaskRow } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, EmptyState, PanelHeader } from "@/components/ui/card";
import { Progress, Skeleton } from "@/components/ui/progress";
import { useSystem } from "@/lib/system";
import { useToast } from "@/components/ui/toast";
import { truncate, timeAgo } from "@/lib/utils";
import { useT, useI18n } from "@/lib/i18n";
import { translateWarning } from "@/lib/i18n/warnings";

export function RunningPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const { host } = useSystem();
  const toast = useToast();
  const t = useT();
  const { locale } = useI18n();

  const load = async () => {
    try {
      const [projectPayload, taskPayload] = await Promise.all([api.listProjects({ sort: "updated" }), api.listTasks()]);
      setProjects(projectPayload.items);
      setTasks(taskPayload.items);
    } catch {
      /* backend may be restarting */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(interval);
  }, []);

  const active = useMemo(
    () => tasks.filter((task) => ["queued", "running", "paused", "waiting_for_user"].includes(task.status)),
    [tasks],
  );
  const runningProjects = useMemo(
    () => projects.filter((project) => project.deploy_status === "RUNNING"),
    [projects],
  );

  const act = async (taskId: string, action: "cancel" | "pause" | "resume" | "retry") => {
    try {
      await api.taskAction(taskId, action);
      const label = action === "cancel" ? t("running.cancel") : action === "pause" ? t("running.pause") : action === "resume" ? t("running.resume") : t("running.retry");
      toast.success(t("running.actionRequested", { action: label }));
      await load();
    } catch (exc) {
      const label = action === "cancel" ? t("running.cancel") : action === "pause" ? t("running.pause") : action === "resume" ? t("running.resume") : t("running.retry");
      toast.error(t("running.actionFailed", { action: label }), exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-5 py-4">
        <div className="mb-3 flex items-end justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-ink">{t("running.title")}</h1>
            <p className="text-xs text-faint">
              {t("running.subtitle")}
            </p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
            {t("common.refresh")}
          </Button>
        </div>

        {host && (!host.docker.available || !host.docker.daemon_running) ? (
          <Card className="mb-3 border-warn/30 bg-warn/[0.05]">
            <div className="px-3.5 py-2.5 text-xs text-warn">
              {t("running.dockerNotReady", {
                error: translateWarning(locale, host.docker.error ?? "", t) || t("shell.dockerMissing"),
              })}
              {host.docker.install_hint ? (
                <span className="text-warn/80"> {translateWarning(locale, host.docker.install_hint, t)}</span>
              ) : null}
            </div>
          </Card>
        ) : null}

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Card>
            <PanelHeader
              title={t("running.liveProjects", { count: runningProjects.length })}
              icon={<Activity className="h-3.5 w-3.5" />}
              subtitle={t("running.liveProjectsHint")}
            />
            {loading ? (
              <div className="space-y-2 p-3.5">
                <Skeleton className="h-10 w-full" />
              </div>
            ) : runningProjects.length === 0 ? (
              <EmptyState
                icon={<Activity className="h-5 w-5" />}
                title={t("running.nothingRunning")}
                description={t("running.nothingRunningDesc")}
              />
            ) : (
              <div className="divide-y divide-hairline">
                {runningProjects.map((project) => {
                  const port = project.environment?.port;
                  return (
                    <div key={project.id} className="flex items-center gap-2 px-3.5 py-2.5">
                      <div className="min-w-0 flex-1">
                        <Link to={`/projects/${project.id}`} className="truncate text-sm text-ink hover:text-[#9dbcff]">
                          {project.display_name || project.name}
                        </Link>
                        <p className="truncate text-2xs text-faint">
                          {project.environment?.framework ?? project.language ?? "unknown stack"}
                          {port ? ` · ${t("running.portLabel", { port })}` : ""}
                        </p>
                      </div>
                      <StatusBadge status={project.deploy_status} />
                      <Button
                        size="icon"
                        variant="ghost"
                        title={t("running.stopProject")}
                        onClick={() => void api.stop(project.id).then(load)}
                      >
                        <Square className="h-3 w-3" />
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          <Card>
            <PanelHeader
              title={t("running.taskQueue", { count: active.length })}
              icon={<Play className="h-3.5 w-3.5" />}
              subtitle={t("running.taskQueueHint")}
            />
            {loading ? (
              <div className="space-y-2 p-3.5">
                <Skeleton className="h-12 w-full" />
              </div>
            ) : active.length === 0 ? (
              <EmptyState title={t("running.queueEmpty")} description={t("running.queueEmptyDesc")} />
            ) : (
              <div className="divide-y divide-hairline">
                {active.map((task) => (
                  <div key={task.id} className="px-3.5 py-2.5">
                    <div className="flex items-center gap-2">
                      <Badge tone="accent">{t(`kind.${task.kind}`)}</Badge>
                      <span className="text-2xs text-faint">#{task.id.slice(0, 6)}</span>
                      <span className="truncate text-2xs text-muted">
                        {truncate(task.message || task.error || task.stage, 60)}
                      </span>
                      <StatusBadge status={task.status} className="ml-auto" />
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      <Progress value={task.progress} className="flex-1" />
                      <span className="w-8 text-right font-mono text-2xs text-faint tabular-nums">
                        {Math.round(task.progress * 100)}%
                      </span>
                      <div className="flex items-center gap-0.5">
                        {task.status === "running" ? (
                          <Button size="icon" variant="ghost" title={t("running.pause")} onClick={() => void act(task.id, "pause")}>
                            <Pause className="h-3 w-3" />
                          </Button>
                        ) : null}
                        {task.status === "paused" || task.status === "waiting_for_user" ? (
                          <Button size="icon" variant="ghost" title={t("running.resume")} onClick={() => void act(task.id, "resume")}>
                            <Play className="h-3 w-3" />
                          </Button>
                        ) : null}
                        {["failed", "cancelled"].includes(task.status) ? (
                          <Button size="icon" variant="ghost" title={t("running.retry")} onClick={() => void act(task.id, "retry")}>
                            <RefreshCw className="h-3 w-3" />
                          </Button>
                        ) : (
                          <Button size="icon" variant="ghost" title={t("running.cancel")} onClick={() => void act(task.id, "cancel")}>
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        )}
                        <Link
                          to={task.project_id ? `/projects/${task.project_id}` : "/projects"}
                          className="rounded p-1 text-faint transition hover:bg-white/5 hover:text-ink"
                          title={t("running.openProject")}
                        >
                          <ExternalLink className="h-3 w-3" />
                        </Link>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <Card className="mt-3">
          <PanelHeader title={t("running.history")} subtitle={t("running.historyHint")} />
          {tasks.length === 0 ? (
            <EmptyState title={t("running.noTasks")} />
          ) : (
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-hairline text-2xs uppercase tracking-wide text-faint">
                  <th className="px-3.5 py-1.5 font-medium">{t("running.tableTask")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("running.tableKind")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("running.tableStatus")}</th>
                  <th className="px-3 py-1.5 font-medium">{t("running.tableMessage")}</th>
                  <th className="px-3.5 py-1.5 font-medium">{t("running.tableCreated")}</th>
                </tr>
              </thead>
              <tbody>
                {tasks.slice(0, 20).map((task) => (
                  <tr key={task.id} className="border-b border-hairline last:border-b-0 text-xs">
                    <td className="px-3.5 py-1.5 font-mono text-2xs text-faint">{task.id.slice(0, 8)}</td>
                    <td className="px-3 py-1.5 text-muted">{t(`kind.${task.kind}`)}</td>
                    <td className="px-3 py-1.5">
                      <StatusBadge status={task.status} />
                    </td>
                    <td className="max-w-[420px] truncate px-3 py-1.5 text-faint">{task.message || task.error || "—"}</td>
                    <td className="px-3.5 py-1.5 text-2xs text-faint">{timeAgo(task.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
