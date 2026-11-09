import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowUpRight,
  Boxes,
  Cpu,
  Github,
  Image as ImageIcon,
  Play,
  Plus,
  Rocket,
  TriangleAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import type { OverviewPayload, ProjectSummary } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, EmptyState, PanelHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/progress";
import { Progress } from "@/components/ui/progress";
import { ImportProjectDialog } from "@/components/layout/ImportProjectDialog";
import { useSystem } from "@/lib/system";
import { formatNumber, timeAgo } from "@/lib/utils";
import { useT, useI18n } from "@/lib/i18n";
import { translateWarning } from "@/lib/i18n/warnings";

function StatPill({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex min-w-0 flex-col border-r border-hairline px-3 py-2 last:border-r-0">
      <span className="text-2xs uppercase tracking-wide text-faint">{label}</span>
      <span className="font-mono text-[15px] tabular-nums text-ink">{value}</span>
      {hint ? <span className="truncate text-2xs text-faint">{hint}</span> : null}
    </div>
  );
}

function ProjectRow({ project }: { project: ProjectSummary }) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="group flex items-center gap-3 border-b border-hairline px-3.5 py-2.5 transition last:border-b-0 hover:bg-white/[0.025]"
    >
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-hairline-strong bg-elevated">
        {project.source_type === "github" ? (
          <Github className="h-3.5 w-3.5 text-muted" />
        ) : (
          <Boxes className="h-3.5 w-3.5 text-muted" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium text-ink">{project.display_name || project.name}</p>
          {project.environment?.framework ? (
            <Badge className="hidden sm:inline-flex">{project.environment.framework}</Badge>
          ) : null}
        </div>
        <p className="truncate text-2xs text-faint">
          {project.source_url || project.description || "no description"}
        </p>
      </div>
      <div className="hidden shrink-0 text-right text-2xs text-faint md:block">
        <p className="tabular-nums">{project.stars ? `${formatNumber(project.stars)} ★` : project.language || "—"}</p>
        <p>{timeAgo(project.updated_at)}</p>
      </div>
      <StatusBadge status={project.deploy_status} />
      <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-faint opacity-0 transition group-hover:opacity-100" />
    </Link>
  );
}

export function OverviewPage() {
  const [data, setData] = useState<OverviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(false);
  const { host } = useSystem();
  const navigate = useNavigate();
  const t = useT();
  const { locale } = useI18n();

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const payload = await api.overview();
        if (active) setData(payload);
      } catch {
        /* backend starting */
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    const interval = window.setInterval(load, 8000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const stats = data?.stats;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-5 py-4">
        <div className="mb-3 flex items-end justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-ink">{t("overview.title")}</h1>
            <p className="text-xs text-faint">
              {t("overview.subtitle")}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <Button variant="secondary" size="sm" onClick={() => navigate("/projects?import=local")}>
              <Plus className="h-3.5 w-3.5" />
              {t("common.import")}
            </Button>
            <Button variant="primary" size="sm" onClick={() => setImportOpen(true)}>
              <Github className="h-3.5 w-3.5" />
              {t("overview.importFromGithub")}
            </Button>
          </div>
        </div>

        {host?.warnings.length ? (
          <div className="mb-3 rounded border border-warn/30 bg-warn/[0.07] px-3 py-2">
            <div className="flex items-start gap-2">
              <TriangleAlert className="mt-[2px] h-3.5 w-3.5 shrink-0 text-warn" />
              <div className="min-w-0">
                {host.warnings.map((warning) => (
                  <p key={warning} className="text-2xs leading-relaxed text-warn/90">
                    {translateWarning(locale, warning, t)}
                  </p>
                ))}
              </div>
              <Link to="/settings" className="ml-auto shrink-0 text-2xs text-warn underline decoration-warn/40">
                {t("overview.settings")}
              </Link>
            </div>
          </div>
        ) : null}

        <Card className="mb-3">
          <div className="grid grid-cols-2 divide-x divide-hairline md:grid-cols-5">
            <StatPill label={t("overview.statProjects")} value={loading ? "—" : String(stats?.projects ?? 0)} />
            <StatPill
              label={t("overview.statRunning")}
              value={loading ? "—" : String(stats?.running ?? 0)}
              hint={stats?.failed ? t("overview.needAttention", { count: stats.failed }) : undefined}
            />
            <StatPill label={t("overview.statAssets")} value={loading ? "—" : String(stats?.assets ?? 0)} hint={t("overview.videosCount", { count: stats?.asset_kinds?.video ?? 0 })} />
            <StatPill label={t("overview.statReports")} value={loading ? "—" : String(stats?.reports ?? 0)} />
            <StatPill
              label={t("overview.statAi")}
              value={loading ? "—" : `${formatNumber(data?.ai_usage_7d.tokens ?? 0)} tok`}
              hint={t("overview.estCost", { cost: (data?.ai_usage_7d.cost_usd ?? 0).toFixed(3) })}
            />
          </div>
        </Card>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_340px]">
          <div className="space-y-3">
            <Card>
              <PanelHeader
                title={t("overview.recentProjects")}
                subtitle={t("overview.recentProjectsHint")}
                icon={<Rocket className="h-3.5 w-3.5" />}
                actions={
                  <Link to="/projects" className="text-2xs text-muted transition hover:text-ink">
                    {t("common.viewAll")}
                  </Link>
                }
              />
              {loading ? (
                <div className="space-y-2 p-3.5">
                  {[0, 1, 2].map((index) => (
                    <Skeleton key={index} className="h-11 w-full" />
                  ))}
                </div>
              ) : data?.recent_projects.length ? (
                <div>
                  {data.recent_projects.map((project) => (
                    <ProjectRow key={project.id} project={project} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  icon={<Github className="h-6 w-6" />}
                  title={t("overview.noProjects")}
                  description={t("overview.noProjectsDesc")}
                  action={
                    <Button variant="primary" size="sm" onClick={() => setImportOpen(true)}>
                      <Github className="h-3.5 w-3.5" />
                      {t("overview.importFirst")}
                    </Button>
                  }
                />
              )}
            </Card>

            <Card>
              <PanelHeader
                title={t("overview.activeTasks")}
                subtitle={t("overview.activeTasksHint")}
                icon={<Activity className="h-3.5 w-3.5" />}
                actions={
                  <Link to="/running" className="text-2xs text-muted transition hover:text-ink">
                    {t("overview.openRunning")}
                  </Link>
                }
              />
              {loading ? (
                <div className="space-y-2 p-3.5">
                  <Skeleton className="h-9 w-full" />
                </div>
              ) : data?.active_tasks.length ? (
                <div className="divide-y divide-hairline">
                  {data.active_tasks.map((task) => (
                    <div key={task.id} className="px-3.5 py-2.5">
                      <div className="flex items-center gap-2">
                        <Badge tone="info">{t(`kind.${task.kind}`)}</Badge>
                        <span className="truncate text-xs text-muted">{task.message || task.stage}</span>
                        <StatusBadge status={task.status} className="ml-auto" />
                      </div>
                      <Progress value={task.progress} className="mt-2" />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="px-3.5 py-4 text-xs text-faint">{t("overview.queueEmpty")}</p>
              )}
            </Card>
          </div>

          <div className="space-y-3">
            <Card>
              <PanelHeader title={t("overview.quickActions")} icon={<Play className="h-3.5 w-3.5" />} />
              <div className="space-y-1 p-1.5">
                {[
                  { label: t("overview.importFromGithub"), onClick: () => setImportOpen(true), icon: Github },
                  { label: t("overview.uploadZip"), onClick: () => setImportOpen(true), icon: Plus },
                  { label: t("overview.browseAssets"), onClick: () => navigate("/assets"), icon: ImageIcon },
                  { label: t("overview.configureModel"), onClick: () => navigate("/providers"), icon: Cpu },
                ].map((action) => (
                  <button
                    key={action.label}
                    type="button"
                    onClick={action.onClick}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-muted transition hover:bg-white/[0.04] hover:text-ink"
                  >
                    <action.icon className="h-3.5 w-3.5 text-faint" />
                    {action.label}
                  </button>
                ))}
              </div>
            </Card>

            <Card>
              <PanelHeader title={t("overview.environment")} icon={<Boxes className="h-3.5 w-3.5" />} />
              <div className="space-y-2 px-3.5 py-3 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-faint">{t("overview.docker")}</span>
                  <Badge tone={host?.docker.available && host.docker.daemon_running ? "ok" : "warn"}>
                    {host?.docker.server_version || translateWarning(locale, host?.docker.error ?? "", t).slice(0, 28) || t("overview.checking")}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-faint">{t("overview.chromium")}</span>
                  <Badge tone={host?.browsers.chromium_available ? "ok" : "warn"}>
                    {host?.browsers.chromium_available ? t("overview.ready") : t("overview.missing")}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-faint">{t("overview.ffmpeg")}</span>
                  <Badge tone={host?.ffmpeg ? "ok" : "neutral"}>{host?.ffmpeg ? t("overview.available") : t("overview.notFound")}</Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-faint">{t("overview.diskFree")}</span>
                  <span className="font-mono text-muted">
                    {host ? `${Math.round(host.disk_free_mb / 1024)} GB` : "—"}
                  </span>
                </div>
              </div>
            </Card>

            <Card>
              <PanelHeader title={t("overview.howItWorks")} />
              <ol className="space-y-1.5 px-3.5 py-3 text-2xs leading-relaxed text-faint">
                <li>{t("overview.step1")}</li>
                <li>{t("overview.step2")}</li>
                <li>{t("overview.step3")}</li>
                <li>{t("overview.step4")}</li>
                <li>{t("overview.step5")}</li>
                <li>{t("overview.step6")}</li>
              </ol>
            </Card>
          </div>
        </div>
      </div>

      <ImportProjectDialog open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  );
}
