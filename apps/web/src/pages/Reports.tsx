import { useCallback, useEffect, useState } from "react";
import { Download, FileText } from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectSummary, ReportRow } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, EmptyState } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { cn, formatDateTime } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function ReportsPage() {
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [active, setActive] = useState<ReportRow | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [busy, setBusy] = useState(false);
  const t = useT();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [reportPayload, projectPayload] = await Promise.all([
        api.listReports(projectId || undefined),
        api.listProjects(),
      ]);
      setReports(reportPayload.items);
      setProjects(projectPayload.items);
      if (reportPayload.items.length && !active) setActive(reportPayload.items[0]);
    } catch {
      setReports([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!active) {
      setMarkdown("");
      return;
    }
    void api.reportMarkdown(active.id).then(setMarkdown).catch(() => setMarkdown("_Could not load report body._"));
  }, [active]);

  const generate = async () => {
    if (!projectId) {
      toast.error(t("reports.selectProject"));
      return;
    }
    setBusy(true);
    try {
      const report = await api.generateReport(projectId);
      toast.success(t("reportPanel.generated"), `${report.status} · v${report.version}`);
      await load();
      setActive(report);
    } catch (exc) {
      toast.error(t("error.reportFailed"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const projectName = (id: string) => projects.find((project) => project.id === id)?.display_name || id.slice(0, 8);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1.5 border-b border-hairline px-4 py-2.5">
        <h1 className="text-sm font-semibold text-ink">{t("reports.title")}</h1>
        <span className="text-2xs text-faint">{t("reports.generated", { count: reports.length })}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Select value={projectId} onChange={(event) => setProjectId(event.target.value)} className="h-7 w-[200px] text-xs">
            <option value="">{t("reports.allProjects")}</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.display_name || project.name}
              </option>
            ))}
          </Select>
          <Button variant="primary" size="sm" loading={busy} onClick={() => void generate()}>
            <FileText className="h-3.5 w-3.5" />
            {t("reports.generate")}
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="w-[280px] shrink-0 overflow-y-auto border-r border-hairline p-2">
          {reports.length === 0 ? (
            <p className="px-1 py-2 text-xs text-faint">{t("reports.noReports")}</p>
          ) : (
            reports.map((report) => (
              <button
                key={report.id}
                type="button"
                onClick={() => setActive(report)}
                className={cn(
                  "mb-1 w-full rounded border px-2 py-1.5 text-left transition",
                  active?.id === report.id ? "border-accent/50 bg-white/[0.05]" : "border-transparent hover:bg-white/[0.03]",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <Badge tone="accent">v{report.version}</Badge>
                  <StatusBadge status={report.status} />
                  <span className="ml-auto text-2xs text-faint">{formatDateTime(report.created_at)}</span>
                </div>
                <p className="mt-1 truncate text-2xs text-muted">{projectName(report.project_id)}</p>
              </button>
            ))
          )}
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto">
          {active ? (
            <div className="p-4">
              <div className="mb-2 flex items-center gap-2">
                <Badge tone="accent">v{active.version}</Badge>
                <StatusBadge status={active.status} />
                <span className="truncate text-xs text-muted">{active.title}</span>
                <div className="ml-auto flex gap-1">
                  {["md", "html", "json"].map((fmt) => (
                    <a
                      key={fmt}
                      href={`/api/reports/${active.id}/download?fmt=${fmt}`}
                      className="flex items-center gap-1 rounded border border-hairline-strong px-1.5 py-0.5 text-2xs text-muted transition hover:text-ink"
                    >
                      <Download className="h-3 w-3" />
                      {fmt.toUpperCase()}
                    </a>
                  ))}
                </div>
              </div>
              <Card>
                <pre className="whitespace-pre-wrap break-words p-3.5 font-sans text-xs leading-relaxed text-muted">
                  {markdown || t("reports.loading")}
                </pre>
              </Card>
            </div>
          ) : (
            <Card className="m-4">
              <EmptyState
                icon={<FileText className="h-6 w-6" />}
                title={t("reports.noneSelected")}
                description={t("reports.noneSelectedDesc")}
              />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
