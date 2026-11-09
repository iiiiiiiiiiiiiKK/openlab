import { useCallback, useEffect, useState } from "react";
import { Cpu, HardDrive, Languages, Lock, Network, Save, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { AppSettings } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, Metric, PanelHeader } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { useSystem } from "@/lib/system";
import { useT, useI18n } from "@/lib/i18n";
import { translateWarning } from "@/lib/i18n/warnings";

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const { host, refreshHost } = useSystem();
  const toast = useToast();
  const t = useT();
  const { locale, setLocale } = useI18n();

  const load = useCallback(async () => {
    try {
      setSettings(await api.getSettings());
    } catch (exc) {
      toast.error(t("error.settingsLoad"), exc instanceof Error ? exc.message : String(exc));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (patch: Record<string, unknown>) => {
    setBusy(true);
    try {
      const next = await api.updateSettings(patch);
      setSettings(next);
      toast.success(t("settings.savedOk"));
      await refreshHost();
    } catch (exc) {
      toast.error(t("error.settingsSave"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const set = (key: keyof AppSettings, value: unknown) => {
    if (!settings) return;
    setSettings({ ...settings, [key]: value } as AppSettings);
  };

  if (!settings) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-faint">{t("settings.loading")}</div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1000px] px-5 py-4">
        <div className="mb-3">
          <h1 className="text-lg font-semibold tracking-tight text-ink">{t("settings.title")}</h1>
          <p className="text-xs text-faint">{t("settings.subtitle")}</p>
        </div>

        <Card className="mb-3">
          <div className="grid grid-cols-2 divide-x divide-hairline md:grid-cols-4">
            <Metric label={t("settings.dataDir")} value={<span className="text-2xs">{settings.data_dir}</span>} />
            <Metric label={t("settings.bindAddress")} value={settings.allow_lan ? "0.0.0.0" : "127.0.0.1"} hint={t("settings.port", { port: settings.port })} />
            <Metric label={t("overview.docker")} value={host?.docker.server_version ?? "n/a"} tone={host?.docker.daemon_running ? "ok" : "warn"} />
            <Metric label={t("settings.chromium")} value={host?.browsers.chromium_available ? t("overview.ready") : t("overview.missing")} tone={host?.browsers.chromium_available ? "ok" : "warn"} />
          </div>
        </Card>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Card>
            <PanelHeader title={t("settings.containerResources")} icon={<Cpu className="h-3.5 w-3.5" />} subtitle={t("settings.containerResourcesHint")} />
            <div className="space-y-2.5 p-3">
              <div className="grid grid-cols-2 gap-2">
                <Field label={t("settings.cpuLimit")} hint={t("settings.cores")}>
                  <Input
                    type="number"
                    step="0.5"
                    value={settings.container_cpu_limit}
                    onChange={(event) => set("container_cpu_limit", Number(event.target.value))}
                  />
                </Field>
                <Field label={t("settings.memoryLimit")} hint={t("settings.mb")}>
                  <Input
                    type="number"
                    value={settings.container_memory_limit_mb}
                    onChange={(event) => set("container_memory_limit_mb", Number(event.target.value))}
                  />
                </Field>
                <Field label={t("settings.pidsLimit")}>
                  <Input
                    type="number"
                    value={settings.container_pids_limit}
                    onChange={(event) => set("container_pids_limit", Number(event.target.value))}
                  />
                </Field>
                <Field label={t("settings.runTimeout")} hint={t("settings.minutes")}>
                  <Input
                    type="number"
                    value={settings.container_run_timeout_min}
                    onChange={(event) => set("container_run_timeout_min", Number(event.target.value))}
                  />
                </Field>
                <Field label={t("settings.concurrency")} hint={t("settings.parallelDeploys")}>
                  <Input
                    type="number"
                    value={settings.max_parallel_deployments}
                    onChange={(event) => set("max_parallel_deployments", Number(event.target.value))}
                  />
                </Field>
                <Field label={t("settings.idleStop")} hint={t("settings.minutes")}>
                  <Input
                    type="number"
                    value={settings.idle_stop_after_min}
                    onChange={(event) => set("idle_stop_after_min", Number(event.target.value))}
                  />
                </Field>
              </div>
              <Button
                variant="primary"
                size="sm"
                loading={busy}
                onClick={() =>
                  void save({
                    container_cpu_limit: settings.container_cpu_limit,
                    container_memory_limit_mb: settings.container_memory_limit_mb,
                    container_pids_limit: settings.container_pids_limit,
                    container_run_timeout_min: settings.container_run_timeout_min,
                    max_parallel_deployments: settings.max_parallel_deployments,
                    idle_stop_after_min: settings.idle_stop_after_min,
                  })
                }
              >
                <Save className="h-3.5 w-3.5" />
                {t("settings.saveResources")}
              </Button>
            </div>
          </Card>

          <Card>
            <PanelHeader title={t("language.label")} subtitle={t("language.hint")} icon={<Languages className="h-3.5 w-3.5" />} />
            <div className="flex items-center gap-2 p-3">
              {(["zh", "en"] as const).map((code) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => setLocale(code)}
                  className={
                    locale === code
                      ? "rounded border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs text-ink"
                      : "rounded border border-hairline bg-canvas px-3 py-1.5 text-xs text-faint hover:text-muted"
                  }
                >
                  {code === "zh" ? t("language.chinese") : t("language.english")}
                </button>
              ))}
            </div>
          </Card>

          <Card>
            <PanelHeader title={t("settings.networkIsolation")} icon={<Network className="h-3.5 w-3.5" />} subtitle={t("settings.networkIsolationHint")} />
            <div className="space-y-2.5 p-3">
              <Toggle
                label={t("settings.allowLan")}
                description={t("settings.allowLanDesc")}
                checked={settings.allow_lan}
                onChange={(value) => void save({ allow_lan: value })}
              />
              <Toggle
                label={t("settings.allowNetwork")}
                description={t("settings.allowNetworkDesc")}
                checked={settings.allow_container_network}
                onChange={(value) => void save({ allow_container_network: value })}
              />
              <Toggle
                label={t("settings.allowHostRuntime")}
                description={t("settings.allowHostRuntimeDesc")}
                checked={settings.allow_host_runtime}
                onChange={(value) => void save({ allow_host_runtime: value })}
                danger
              />
              <div className="rounded border border-hairline bg-canvas px-2.5 py-2">
                <p className="mb-1 flex items-center gap-1 text-2xs text-muted">
                  <ShieldCheck className="h-3 w-3 text-ok" /> {t("settings.baseline")}
                </p>
                <ul className="space-y-0.5 text-2xs text-faint">
                  <li>• {t("settings.baseline1")}</li>
                  <li>• {t("settings.baseline2")}</li>
                  <li>• {t("settings.baseline3")}</li>
                </ul>
              </div>
            </div>
          </Card>

          <Card>
            <PanelHeader title={t("settings.credentials")} icon={<Lock className="h-3.5 w-3.5" />} subtitle={t("settings.credentialsHint")} />
            <div className="space-y-2.5 p-3">
              <Field label={t("settings.githubToken")} hint={settings.has_github_token ? t("settings.saved", { mask: settings.github_token_masked ?? "" }) : t("settings.notSet")}>
                <Input
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="ghp_…"
                />
              </Field>
              <div className="flex gap-1.5">
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy}
                  onClick={() => {
                    if (!token.trim()) {
                      toast.error(t("settings.enterToken"));
                      return;
                    }
                    void save({ github_token: token.trim() });
                    setToken("");
                  }}
                >
                  {t("settings.saveToken")}
                </Button>
                <Button variant="danger" size="sm" onClick={() => void save({ clear_github_token: true })}>
                  {t("settings.clear")}
                </Button>
              </div>
              <p className="text-2xs leading-relaxed text-faint">
                {t("settings.masterKeyNote", { path: `${settings.data_dir}\\secrets\\master.key` })}
              </p>
            </div>
          </Card>

          <Card>
            <PanelHeader title={t("settings.hostReadiness")} icon={<HardDrive className="h-3.5 w-3.5" />} subtitle={t("settings.hostReadinessHint")} />
            <div className="space-y-1.5 p-3 text-xs">
              {[
                [t("settings.dockerCli"), host?.docker.available ? host.docker.client_version ?? t("overview.ready") : t("overview.missing")],
                [t("settings.dockerDaemon"), host?.docker.daemon_running ? t("settings.running") : t("settings.stopped")],
                [t("settings.compose"), host?.docker.compose_version ?? t("settings.notDetected")],
                [t("settings.playwrightChromium"), host?.browsers.chromium_available ? host.browsers.chromium_bundled.join(", ") || t("settings.systemBrowser") : t("overview.missing")],
                [t("overview.ffmpeg"), host?.ffmpeg ? t("overview.available") : t("overview.notFound")],
                [t("settings.cpuCores"), String(host?.cpu_count ?? "—")],
                [t("settings.memory"), host ? `${Math.round(host.memory_total_mb / 1024)} GB` : "—"],
                [t("settings.diskFree"), host ? `${Math.round(host.disk_free_mb / 1024)} GB` : "—"],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between gap-2 border-b border-hairline pb-1 last:border-b-0">
                  <span className="text-faint">{label}</span>
                  <span className="truncate text-muted">{value}</span>
                </div>
              ))}
              {host?.warnings.length ? (
                <div className="mt-1 space-y-0.5">
                  {host.warnings.map((warning) => (
                    <p key={warning} className="text-2xs text-warn">
                      • {translateWarning(locale, warning, t)}
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          </Card>

          <Card className="lg:col-span-2">
            <PanelHeader title={t("settings.browserAutomation")} subtitle={t("settings.browserAutomationHint")} />
            <div className="grid grid-cols-1 gap-2.5 p-3 sm:grid-cols-3">
              <Field label={t("settings.browserChannel")} hint={t("settings.browserChannelHint")}>
                <Select
                  value={settings.browser_channel ?? ""}
                  onChange={(event) => void save({ browser_channel: event.target.value || null })}
                >
                  <option value="">{t("settings.bundledChromium")}</option>
                  <option value="chrome">{t("settings.systemChrome")}</option>
                  <option value="edge">{t("settings.systemEdge")}</option>
                </Select>
              </Field>
              <Field label={t("settings.repairAttempts")} hint={t("settings.repairAttemptsHint")}>
                <Input
                  type="number"
                  value={settings.max_repair_attempts}
                  onChange={(event) => set("max_repair_attempts", Number(event.target.value))}
                />
              </Field>
              <Field label={t("settings.maxDownload")} hint={t("settings.maxDownloadHint")}>
                <Input
                  type="number"
                  value={settings.max_download_mb}
                  onChange={(event) => set("max_download_mb", Number(event.target.value))}
                />
              </Field>
              <div className="sm:col-span-3">
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy}
                  onClick={() =>
                    void save({
                      browser_channel: settings.browser_channel || null,
                      max_repair_attempts: settings.max_repair_attempts,
                      max_download_mb: settings.max_download_mb,
                    })
                  }
                >
                  <Save className="h-3.5 w-3.5" />
                  {t("settings.saveAutomation")}
                </Button>
                <Badge className="ml-2">{host?.browsers.browsers_path ?? t("settings.noBrowserPath")}</Badge>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Toggle({
  label,
  description,
  checked,
  onChange,
  danger,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  danger?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded border border-hairline bg-canvas px-2.5 py-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-[3px] h-3.5 w-3.5 accent-[#5b8cff]"
      />
      <span className="min-w-0">
        <span className={danger && checked ? "text-xs text-danger" : "text-xs text-muted"}>{label}</span>
        <span className="block text-2xs leading-relaxed text-faint">{description}</span>
      </span>
    </label>
  );
}
