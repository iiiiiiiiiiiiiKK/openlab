import { useEffect, useState } from "react";
import { AlertTriangle, FileDiff, RefreshCw, Sparkles, Undo2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { cn, formatDateTime, truncate } from "@/lib/utils";
import { useT } from "@/lib/i18n";

interface PatchRow {
  id: string;
  attempt: number;
  diagnosis: string | null;
  files_touched: string[];
  applied: boolean;
  rolled_back: boolean;
  diff: string | null;
  created_at: string | null;
}

export function AgentPanel({
  project,
  onRefresh,
}: {
  project: ProjectDetail;
  onRefresh: () => void;
}) {
  const [patches, setPatches] = useState<PatchRow[]>([]);
  const [openDiff, setOpenDiff] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const t = useT();

  const load = () => {
    void api
      .patches(project.id)
      .then((payload) => setPatches(payload.items))
      .catch(() => setPatches([]));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  const plan = project.plan ?? {};
  const evidence = plan.evidence ?? {};

  const reset = async () => {
    if (!window.confirm(t("agent.confirmRestore"))) return;
    setBusy(true);
    try {
      await api.resetWorkspace(project.id);
      load();
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-hairline bg-surface px-2.5 py-1.5">
        <Sparkles className="h-3 w-3 text-faint" />
        <span className="text-2xs text-muted">{t("agent.title")}</span>
        <span className="text-2xs text-faint">{t("agent.subtitle")}</span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="icon" variant="ghost" onClick={load} title={t("agent.reloadPatches")}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="secondary" loading={busy} onClick={() => void reset()}>
            <Undo2 className="h-3 w-3" />
            {t("agent.restoreSource")}
          </Button>
        </div>
      </div>

      <div className="scroll-area min-h-0 flex-1 divide-y divide-hairline">
        <section className="px-3 py-2.5">
          <p className="mb-1.5 text-2xs uppercase tracking-wide text-faint">{t("agent.runPlan")}</p>
          <div className="grid grid-cols-2 gap-1.5 text-xs">
            {[
              [t("agent.type"), plan.project_type],
              [t("agent.framework"), plan.framework],
              [t("agent.packageManager"), plan.package_manager],
              [t("agent.runtime"), plan.runtime_version],
              [t("agent.strategy"), plan.strategy],
              [t("agent.port"), plan.port],
              [t("agent.baseImage"), plan.base_image],
              [t("agent.confidence"), plan.confidence !== undefined ? `${Math.round((plan.confidence ?? 0) * 100)}%` : undefined],
            ].map(([label, value]) => (
              <div key={label} className="rounded border border-hairline bg-canvas px-2 py-1.5">
                <p className="text-2xs text-faint">{label}</p>
                <p className="truncate font-mono text-2xs text-muted">{String(value ?? "—")}</p>
              </div>
            ))}
          </div>
          {plan.start_command ? (
            <div className="mt-1.5 rounded border border-hairline bg-canvas px-2 py-1.5">
              <p className="text-2xs text-faint">{t("agent.startCommand")}</p>
              <p className="break-all font-mono text-2xs text-muted">{plan.start_command}</p>
            </div>
          ) : null}
          {Object.entries(evidence).length ? (
            <div className="mt-1.5 space-y-0.5">
              <p className="text-2xs uppercase tracking-wide text-faint">{t("agent.evidence")}</p>
              {Object.entries(evidence)
                .slice(0, 6)
                .map(([key, value]) => (
                  <p key={key} className="truncate text-2xs text-faint">
                    <span className="text-muted">{key}</span> = {truncate(String(value.value ?? "—"), 46)} ·{" "}
                    <span className="text-faint">{value.source}</span>
                  </p>
                ))}
            </div>
          ) : null}
          {plan.warnings?.length ? (
            <div className="mt-1.5 rounded border border-warn/30 bg-warn/[0.06] px-2 py-1.5">
              {plan.warnings.map((warning) => (
                <p key={warning} className="text-2xs leading-relaxed text-warn/90">
                  • {warning}
                </p>
              ))}
            </div>
          ) : null}
          {plan.project_type === "native" ? (
            <div className="mt-1.5 rounded border border-info/30 bg-info/[0.06] px-2 py-1.5">
              <p className="text-2xs font-medium text-info">{t("agent.nativeProject")}</p>
              <p className="mt-0.5 text-2xs leading-relaxed text-muted">{t("agent.nativeProjectHint")}</p>
            </div>
          ) : null}
          {plan.blocking_requirements?.length ? (
            <div className="mt-1.5 rounded border border-danger/30 bg-danger/[0.07] px-2 py-1.5">
              {plan.blocking_requirements.map((item) => (
                <p key={item} className="text-2xs leading-relaxed text-danger">
                  • {item}
                </p>
              ))}
            </div>
          ) : null}
        </section>

        <section className="px-3 py-2.5">
          <p className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wide text-faint">
            <FileDiff className="h-3 w-3" /> {t("agent.patches", { count: patches.length })}
          </p>
          {patches.length === 0 ? (
            <p className="text-2xs leading-relaxed text-faint">
              {t("agent.noPatches")}
            </p>
          ) : (
            <div className="space-y-1.5">
              {patches.map((patch) => (
                <div key={patch.id} className="rounded border border-hairline bg-canvas">
                  <button
                    type="button"
                    onClick={() => setOpenDiff(openDiff === patch.id ? null : patch.id)}
                    className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left"
                  >
                    <Badge tone="accent">#{patch.attempt}</Badge>
                    <span className="truncate text-2xs text-muted">{patch.diagnosis || t("agent.noDiagnosis")}</span>
                    <StatusBadge status={patch.rolled_back ? "CANCELLED" : patch.applied ? "VERIFIED" : "FAILED"} className="ml-auto" />
                  </button>
                  <div className="px-2 pb-1.5">
                    <p className="text-2xs text-faint">
                      {patch.files_touched.join(", ") || t("agent.noFiles")} · {formatDateTime(patch.created_at)}
                    </p>
                    {openDiff === patch.id && patch.diff ? (
                      <pre className={cn("mt-1 max-h-[220px] overflow-auto rounded border border-hairline bg-[#07080a] p-2 font-mono text-[10.5px] leading-[1.5]")}>
                        {patch.diff.split("\n").map((line, index) => (
                          <div
                            key={index}
                            className={cn(
                              line.startsWith("+") && !line.startsWith("+++") && "text-ok",
                              line.startsWith("-") && !line.startsWith("---") && "text-danger",
                              line.startsWith("@") && "text-info",
                              !line.startsWith("+") && !line.startsWith("-") && !line.startsWith("@") && "text-muted",
                            )}
                          >
                            {line || " "}
                          </div>
                        ))}
                      </pre>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {(project.plan?.gpu_required || project.last_error) && (
          <section className="px-3 py-2.5">
            <p className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wide text-faint">
              <AlertTriangle className="h-3 w-3" /> {t("agent.diagnostics")}
            </p>
            {project.plan?.gpu_required ? (
              <p className="text-2xs leading-relaxed text-warn">
                {t("agent.gpuDetected", { notes: (project.plan.gpu_notes ?? []).join("; ") || t("agent.seeReadme") })}
              </p>
            ) : null}
            {project.last_error ? (
              <p className="mt-1 break-words text-2xs leading-relaxed text-danger">{project.last_error}</p>
            ) : null}
          </section>
        )}
      </div>
    </div>
  );
}
