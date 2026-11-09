import { useCallback, useEffect, useState } from "react";
import { Download, FileText, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { ReportRow } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { formatDateTime } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function ReportPanel({ projectId }: { projectId: string }) {
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [active, setActive] = useState<ReportRow | null>(null);
  const [markdown, setMarkdown] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const t = useT();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const payload = await api.listReports(projectId);
      setReports(payload.items);
      if (payload.items.length && !active) setActive(payload.items[0]);
    } catch {
      setReports([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (!active) {
      setMarkdown("");
      return;
    }
    void api
      .reportMarkdown(active.id)
      .then(setMarkdown)
      .catch(() => setMarkdown(`_${t("reportPanel.bodyFailed")}_`));
  }, [active]);

  const generate = async () => {
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

  return (
    <div className="flex h-full min-h-0">
      <div className="flex w-[230px] shrink-0 flex-col border-r border-hairline bg-surface/40">
        <div className="flex items-center gap-1.5 border-b border-hairline px-2 py-1.5">
          <FileText className="h-3 w-3 text-faint" />
          <span className="text-2xs text-muted">{t("reportPanel.title")}</span>
          <Button size="icon" variant="ghost" className="ml-auto" onClick={() => void load()}>
            <RefreshCw className="h-3 w-3" />
          </Button>
        </div>
        <div className="scroll-area min-h-0 flex-1 p-1.5">
          {reports.length === 0 ? (
            <p className="px-1 py-2 text-2xs leading-relaxed text-faint">
              {t("reportPanel.empty")}
            </p>
          ) : (
            reports.map((report) => (
              <button
                key={report.id}
                type="button"
                onClick={() => setActive(report)}
                className={`mb-1 w-full rounded border px-2 py-1.5 text-left transition ${
                  active?.id === report.id ? "border-accent/50 bg-white/[0.05]" : "border-transparent hover:bg-white/[0.03]"
                }`}
              >
                <div className="flex items-center gap-1">
                  <span className="text-2xs text-muted">v{report.version}</span>
                  <StatusBadge status={report.status} className="ml-auto" />
                </div>
                <p className="mt-0.5 truncate text-[10px] text-faint">{formatDateTime(report.created_at)}</p>
              </button>
            ))
          )}
        </div>
        <div className="border-t border-hairline p-1.5">
          <Button variant="primary" size="sm" className="w-full" loading={busy} onClick={() => void generate()}>
            {t("reportPanel.generate")}
          </Button>
        </div>
      </div>

      <div className="min-w-0 flex-1 overflow-auto bg-canvas">
        {active ? (
          <div>
            <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-hairline bg-surface px-2.5 py-1.5">
              <Badge tone="accent">v{active.version}</Badge>
              <StatusBadge status={active.status} />
              <span className="truncate text-2xs text-faint">{active.title}</span>
              <div className="ml-auto flex items-center gap-1">
                {[
                  { fmt: "md", label: "MD" },
                  { fmt: "html", label: "HTML" },
                  { fmt: "json", label: "JSON" },
                ].map((item) => (
                  <a
                    key={item.fmt}
                    href={`/api/reports/${active.id}/download?fmt=${item.fmt}`}
                    className="flex items-center gap-1 rounded border border-hairline-strong px-1.5 py-0.5 text-2xs text-muted transition hover:text-ink"
                  >
                    <Download className="h-3 w-3" />
                    {item.label}
                  </a>
                ))}
              </div>
            </div>
            <pre className="whitespace-pre-wrap break-words p-4 font-sans text-xs leading-relaxed text-muted">
              {markdown || t("reportPanel.loading")}
            </pre>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <FileText className="h-5 w-5 text-faint" />
            <p className="text-xs text-muted">{t("reportPanel.noneSelected")}</p>
            <p className="max-w-xs text-2xs text-faint">
              {t("reportPanel.noneSelectedDesc")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
