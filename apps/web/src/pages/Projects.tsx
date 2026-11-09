import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Github, Filter, Play, Plus, RefreshCw, Search, Star, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, EmptyState } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/progress";
import { ImportProjectDialog } from "@/components/layout/ImportProjectDialog";
import { useToast } from "@/components/ui/toast";
import { cn, formatNumber, timeAgo } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("updated");
  const [importOpen, setImportOpen] = useState(params.get("import") !== null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const toast = useToast();
  const t = useT();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api.listProjects({ q: query || undefined, status: status || undefined, sort });
      setProjects(payload.items);
    } catch (exc) {
      toast.error(t("error.loadProjects"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, status, sort]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (params.get("import")) {
      setImportOpen(true);
      params.delete("import");
      setParams(params, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    projects.forEach((project) => {
      map[project.deploy_status] = (map[project.deploy_status] ?? 0) + 1;
    });
    return map;
  }, [projects]);

  const launch = async (project: ProjectSummary) => {
    setBusyId(project.id);
    try {
      const result = await api.deploy(project.id);
      toast.success(t("projects.deployQueued"), `task ${result.task_id.slice(0, 8)} · ${project.name}`);
      void load();
    } catch (exc) {
      toast.error(t("error.deployQueue"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (project: ProjectSummary) => {
    if (!window.confirm(t("projects.confirmDelete", { name: project.display_name || project.name }))) return;
    try {
      await api.deleteProject(project.id);
      toast.success(t("projects.deleted"));
      void load();
    } catch (exc) {
      toast.error(t("error.deleteProject"), exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
        <h1 className="text-sm font-semibold text-ink">{t("projects.title")}</h1>
        <span className="text-2xs text-faint">{t("projects.shown", { count: projects.length })}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("projects.searchPlaceholder")}
              className="h-7 w-[240px] pl-7 text-xs"
            />
          </div>
          <Select value={status} onChange={(event) => setStatus(event.target.value)} className="h-7 w-[150px] text-xs">
            <option value="">{t("projects.allStatuses")}</option>
            <option value="RUNNING">{t("nav.running")}</option>
            <option value="FAILED">{t("status.FAILED")}</option>
            <option value="WAITING_FOR_USER">{t("status.WAITING_FOR_USER")}</option>
            <option value="PENDING">{t("status.PENDING")}</option>
            <option value="STOPPED">{t("status.STOPPED")}</option>
          </Select>
          <Select value={sort} onChange={(event) => setSort(event.target.value)} className="h-7 w-[130px] text-xs">
            <option value="updated">{t("projects.sortUpdated")}</option>
            <option value="created">{t("projects.sortCreated")}</option>
            <option value="name">{t("projects.sortName")}</option>
            <option value="stars">{t("projects.sortStars")}</option>
          </Select>
          <Button variant="ghost" size="icon" onClick={() => void load()} title={t("common.refresh")}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button variant="primary" size="sm" onClick={() => setImportOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
            {t("common.import")}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-hairline px-4 py-1.5">
        <Filter className="h-3 w-3 text-faint" />
        {Object.entries(counts).length ? (
          Object.entries(counts).map(([key, value]) => (
            <Badge key={key}>
              {key.toLowerCase()} <span className="text-faint">{value}</span>
            </Badge>
          ))
        ) : (
          <span className="text-2xs text-faint">{t("projects.noData")}</span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-16 w-full" />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Github className="h-6 w-6" />}
              title={t("projects.empty")}
              description={t("projects.emptyDesc")}
              action={
                <Button variant="primary" size="sm" onClick={() => setImportOpen(true)}>
                  <Plus className="h-3.5 w-3.5" />
                  {t("projects.importProject")}
                </Button>
              }
            />
          </Card>
        ) : (
          <Card className="overflow-hidden">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-hairline text-2xs uppercase tracking-wide text-faint">
                  <th className="px-3.5 py-2 font-medium">{t("projects.tableProject")}</th>
                  <th className="px-3 py-2 font-medium">{t("projects.tableStack")}</th>
                  <th className="px-3 py-2 font-medium">{t("projects.tableStatus")}</th>
                  <th className="px-3 py-2 font-medium">{t("projects.tableAssets")}</th>
                  <th className="px-3 py-2 font-medium">{t("projects.tableUpdated")}</th>
                  <th className="px-3.5 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.id} className="border-b border-hairline last:border-b-0 hover:bg-white/[0.02]">
                    <td className="px-3.5 py-2.5">
                      <Link to={`/projects/${project.id}`} className="group block min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-medium text-ink group-hover:text-[#9dbcff]">
                            {project.display_name || project.name}
                          </p>
                          {project.favorite ? <Star className="h-3 w-3 fill-warn text-warn" /> : null}
                        </div>
                        <p className="max-w-[420px] truncate text-2xs text-faint">
                          {project.source_url || project.description || "—"}
                        </p>
                      </Link>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex flex-wrap items-center gap-1">
                        {project.language ? <Badge>{project.language}</Badge> : null}
                        {project.environment?.framework ? <Badge tone="accent">{project.environment.framework}</Badge> : null}
                        {project.environment?.gpu_required ? <Badge tone="warn">GPU</Badge> : null}
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusBadge status={project.deploy_status} />
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-muted tabular-nums">{project.asset_count}</td>
                    <td className="px-3 py-2.5 text-2xs text-faint">{timeAgo(project.updated_at)}</td>
                    <td className="px-3.5 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={busyId === project.id}
                          onClick={() => void launch(project)}
                        >
                          <Play className="h-3 w-3" />
                          {t("common.run")}
                        </Button>
                        <Button size="icon" variant="ghost" onClick={() => void remove(project)} title={t("common.delete")}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}

        {!loading && projects.length > 0 ? (
          <p className={cn("mt-2 text-2xs text-faint")}>
            {t("projects.starsAcross", {
              stars: formatNumber(projects.reduce((sum, project) => sum + project.stars, 0)),
              count: projects.length,
            })}
          </p>
        ) : null}
      </div>

      <ImportProjectDialog open={importOpen} onClose={() => setImportOpen(false)} onImported={() => void load()} />
    </div>
  );
}
