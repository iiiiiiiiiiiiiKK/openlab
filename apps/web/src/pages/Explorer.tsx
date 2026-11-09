import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Camera, CheckCircle2, Circle, Play, Sparkles, TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import type { ExplorationPlanRow, ProjectSummary } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, EmptyState, PanelHeader } from "@/components/ui/card";
import { Field, Select, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { cn, formatDateTime } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function ExplorerPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [mode, setMode] = useState<"quick" | "deep" | "custom">("quick");
  const [goal, setGoal] = useState("");
  const [recordVideo, setRecordVideo] = useState(true);
  const [plans, setPlans] = useState<ExplorationPlanRow[]>([]);
  const [activePlan, setActivePlan] = useState<ExplorationPlanRow | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const t = useT();

  useEffect(() => {
    void api
      .listProjects({ sort: "updated" })
      .then((payload) => {
        setProjects(payload.items);
        const running = payload.items.find((project) => project.deploy_status === "RUNNING");
        setProjectId((current) => current || running?.id || payload.items[0]?.id || "");
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!projectId) return;
    void api
      .explorePlans(projectId)
      .then((payload) => {
        setPlans(payload.items);
        setActivePlan(payload.items[0] ?? null);
      })
      .catch(() => {
        setPlans([]);
        setActivePlan(null);
      });
  }, [projectId]);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === projectId),
    [projectId, projects],
  );
  const canExplore = selectedProject?.deploy_status === "RUNNING";

  const start = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      const result = await api.explore(projectId, {
        mode,
        goal: mode === "custom" ? goal : undefined,
        record_video: recordVideo,
      });
      toast.success(t("explorer.queued"), t("explorer.queuedDesc", { id: result.task_id.slice(0, 8) }));
    } catch (exc) {
      toast.error(t("error.exploreStart"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const previewPlan = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      const result = await api.explorePlan(projectId, { mode, goal: mode === "custom" ? goal : undefined });
      toast.success(t("explorer.planGenerated"), t("explorer.steps", { count: (result.plan.steps as unknown[] | undefined)?.length ?? 0 }));
      const refreshed = await api.explorePlans(projectId);
      setPlans(refreshed.items);
      setActivePlan(refreshed.items[0] ?? null);
    } catch (exc) {
      toast.error(t("error.planFailed"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const planSteps = activePlan?.steps ?? activePlan?.plan.steps ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-5 py-4">
        <div className="mb-3">
          <h1 className="text-lg font-semibold tracking-tight text-ink">{t("explorer.title")}</h1>
          <p className="text-xs text-faint">
            {t("explorer.subtitle")}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[360px_1fr]">
          <Card>
            <PanelHeader title={t("explorer.newExploration")} icon={<Sparkles className="h-3.5 w-3.5" />} />
            <div className="space-y-2.5 p-3">
              <Field label={t("explorer.project")}>
                <Select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                  {projects.length === 0 ? <option value="">{t("explorer.noProjects")}</option> : null}
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.display_name || project.name} · {project.deploy_status.toLowerCase()}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label={t("explorer.mode")}>
                <div className="grid grid-cols-3 gap-1">
                  {[
                    { id: "quick", label: t("explorer.quick") },
                    { id: "deep", label: t("explorer.deep") },
                    { id: "custom", label: t("explorer.custom") },
                  ].map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setMode(item.id as typeof mode)}
                      className={cn(
                        "rounded border px-2 py-1.5 text-2xs transition",
                        mode === item.id
                          ? "border-accent/50 bg-accent/10 text-ink"
                          : "border-hairline bg-canvas text-faint hover:text-muted",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </Field>

              {mode === "custom" ? (
                <Field label={t("explorer.goal")} hint={t("explorer.goalHint")}>
                  <Textarea
                    rows={3}
                    value={goal}
                    onChange={(event) => setGoal(event.target.value)}
                    placeholder={t("explorer.goalPlaceholder")}
                  />
                </Field>
              ) : (
                <p className="text-2xs leading-relaxed text-faint">
                  {mode === "quick" ? t("explorer.quickDesc") : t("explorer.deepDesc")}
                </p>
              )}

              <label className="flex items-center gap-2 text-2xs text-muted">
                <input
                  type="checkbox"
                  checked={recordVideo}
                  onChange={(event) => setRecordVideo(event.target.checked)}
                  className="h-3 w-3 accent-[#5b8cff]"
                />
                {t("explorer.recordVideo")}
              </label>

              {!canExplore ? (
                <div className="flex items-start gap-1.5 rounded border border-warn/30 bg-warn/[0.06] px-2 py-1.5 text-2xs text-warn">
                  <TriangleAlert className="mt-[1px] h-3 w-3 shrink-0" />
                  {t("explorer.notRunning")}
                </div>
              ) : null}

              <div className="flex gap-1.5 pt-0.5">
                <Button variant="secondary" size="sm" className="flex-1" loading={busy} disabled={!canExplore} onClick={() => void previewPlan()}>
                  {t("explorer.planOnly")}
                </Button>
                <Button variant="primary" size="sm" className="flex-1" loading={busy} disabled={!canExplore} onClick={() => void start()}>
                  <Play className="h-3 w-3" />
                  {t("explorer.run")}
                </Button>
              </div>
            </div>
          </Card>

          <div className="space-y-3">
            <Card>
              <PanelHeader
                title={activePlan ? t("explorer.planTitle", { mode: activePlan.mode, status: activePlan.status }) : t("explorer.noPlan")}
                subtitle={activePlan?.summary ?? t("explorer.noPlanDesc")}
                icon={<Camera className="h-3.5 w-3.5" />}
                actions={
                  activePlan ? (
                    <Badge tone={activePlan.generated_by === "model" ? "accent" : "neutral"}>
                      {activePlan.generated_by}
                    </Badge>
                  ) : null
                }
              />
              {plans.length === 0 ? (
                <EmptyState
                  icon={<Sparkles className="h-5 w-5" />}
                  title={t("explorer.noExplorations")}
                  description={t("explorer.noExplorationsDesc")}
                />
              ) : (
                <div className="max-h-[360px] divide-y divide-hairline overflow-y-auto">
                  {planSteps.map((step, index) => {
                    const record = step as {
                      index?: number;
                      action?: string;
                      target?: string | null;
                      description?: string | null;
                      status?: string;
                      error?: string | null;
                      duration_ms?: number | null;
                    };
                    const status = record.status ?? "pending";
                    return (
                      <div key={`${activePlan?.id}-${index}`} className="flex items-start gap-2 px-3 py-1.5">
                        <span className="mt-[2px]">
                          {status === "VERIFIED" || status === "PARTIALLY_VERIFIED" ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-ok" />
                          ) : status === "FAILED" ? (
                            <TriangleAlert className="h-3.5 w-3.5 text-danger" />
                          ) : (
                            <Circle className="h-3.5 w-3.5 text-faint" />
                          )}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-2xs text-muted">
                            <span className="font-mono text-faint">{String(record.action ?? "step")}</span>{" "}
                            {record.description || record.target}
                          </p>
                          {record.error ? <p className="truncate text-[10px] text-danger">{record.error}</p> : null}
                        </div>
                        <StatusBadge status={status} />
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>

            <Card>
              <PanelHeader title={t("explorer.history")} subtitle={t("explorer.historyHint")} />
              {plans.length === 0 ? (
                <p className="px-3.5 py-3 text-xs text-faint">{t("explorer.nothingRecorded")}</p>
              ) : (
                <div className="divide-y divide-hairline">
                  {plans.map((plan) => (
                    <button
                      key={plan.id}
                      type="button"
                      onClick={() => setActivePlan(plan)}
                      className={cn(
                        "flex w-full items-center gap-2 px-3.5 py-2 text-left transition hover:bg-white/[0.03]",
                        activePlan?.id === plan.id && "bg-white/[0.035]",
                      )}
                    >
                      <Badge>{plan.mode}</Badge>
                      <span className="truncate text-2xs text-muted">{plan.summary || plan.goal || plan.id.slice(0, 8)}</span>
                      <span className="ml-auto shrink-0 text-2xs text-faint">{formatDateTime(plan.created_at)}</span>
                      <StatusBadge status={plan.status} />
                    </button>
                  ))}
                </div>
              )}
            </Card>

            <Card>
              <PanelHeader title={t("explorer.safetyRules")} />
              <ul className="space-y-1 px-3.5 py-3 text-2xs leading-relaxed text-faint">
                <li>• {t("explorer.rule1")}</li>
                <li>• {t("explorer.rule2")}</li>
                <li>• {t("explorer.rule3")}</li>
                <li>• {t("explorer.rule4")}</li>
                <li>
                  {t("explorer.rule5")}{" "}
                  <Link to="/settings" className="text-[#9dbcff] hover:underline">
                    Open a headed browser session
                  </Link>{" "}
                  from a project page and log in manually.
                </li>
              </ul>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
