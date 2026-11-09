import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import {
  Activity,
  Boxes,
  Cpu,
  FlaskConical,
  FolderKanban,
  Gauge,
  Image,
  Search,
  Settings,
  Sparkles,
  FileText,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useT, useI18n } from "@/lib/i18n";
import { SystemProvider, useSystem } from "@/lib/system";
import { StatusDot } from "@/components/ui/badge";
import { CommandPalette } from "@/components/layout/CommandPalette";

/** The author's X (Twitter) glyph in a 24x24 viewBox. */
function XIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor" className={className}>
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

const NAV = [
  { to: "/", labelKey: "nav.overview", icon: Gauge, end: true },
  { to: "/projects", labelKey: "nav.projects", icon: FolderKanban },
  { to: "/running", labelKey: "nav.running", icon: Activity },
  { to: "/explorer", labelKey: "nav.explorer", icon: Sparkles },
  { to: "/assets", labelKey: "nav.assets", icon: Image },
  { to: "/reports", labelKey: "nav.reports", icon: FileText },
  { to: "/providers", labelKey: "nav.providers", icon: Boxes },
  { to: "/settings", labelKey: "nav.settings", icon: Settings },
];

function Sidebar() {
  const t = useT();
  return (
    <aside className="flex w-[208px] shrink-0 flex-col border-r border-hairline bg-surface">
      <div className="flex h-[46px] items-center gap-2 border-b border-hairline px-3.5">
        <div className="flex h-6 w-6 items-center justify-center rounded border border-accent/40 bg-accent/15">
          <FlaskConical className="h-3.5 w-3.5 text-[#8fb2ff]" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight text-ink">OPENLAB</p>
          <p className="text-[9.5px] uppercase tracking-[0.14em] text-faint">{t("app.tagline")}</p>
        </div>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-2.5">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                "group flex items-center gap-2.5 rounded px-2 py-[7px] text-sm transition-colors",
                isActive
                  ? "bg-white/[0.055] text-ink"
                  : "text-muted hover:bg-white/[0.03] hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                <item.icon
                  className={cn("h-[15px] w-[15px]", isActive ? "text-[#8fb2ff]" : "text-faint group-hover:text-muted")}
                />
                <span className="truncate">{t(item.labelKey)}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-hairline px-3 py-2.5">
        <p className="text-[10px] leading-relaxed text-faint">{t("app.localOnly")}</p>
      </div>
    </aside>
  );
}

function LanguageSwitch() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div
      className="flex h-7 items-center gap-0.5 rounded border border-hairline-strong bg-canvas p-0.5"
      title={t("language.hint")}
    >
      {(["zh", "en"] as const).map((code) => (
        <button
          key={code}
          type="button"
          data-testid={`locale-${code}`}
          onClick={() => setLocale(code)}
          aria-pressed={locale === code}
          className={cn(
            "rounded-sm px-1.5 py-0.5 text-2xs transition",
            locale === code ? "bg-white/[0.09] text-ink" : "text-faint hover:text-muted",
          )}
        >
          {code === "zh" ? "中文" : "EN"}
        </button>
      ))}
    </div>
  );
}

function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { host, tasks, events } = useSystem();
  const navigate = useNavigate();
  const t = useT();
  const running = tasks.filter((task) => task.status === "running").length;
  const queued = tasks.filter((task) => task.status === "queued").length;
  const dockerOk = host?.docker.available && host.docker.daemon_running;
  const browserOk = host?.browsers.chromium_available ?? false;
  const latest = events[events.length - 1];

  return (
    <header className="flex h-[46px] shrink-0 items-center gap-3 border-b border-hairline bg-surface/80 px-3.5 backdrop-blur">
      <button
        type="button"
        onClick={onOpenPalette}
        className="group flex h-7 w-[260px] items-center gap-2 rounded border border-hairline-strong bg-canvas px-2 text-left text-xs text-faint transition hover:border-[#333840] hover:text-muted"
      >
        <Search className="h-3.5 w-3.5" />
        <span className="flex-1">{t("shell.search")}</span>
        <span className="kbd">⌘K</span>
      </button>

      <div className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => navigate("/running")}
          className="flex h-7 items-center gap-1.5 rounded border border-hairline-strong bg-canvas px-2 text-2xs text-muted transition hover:text-ink"
          title={`${t("shell.running", { running })} · ${t("shell.queued", { queued })}`}
        >
          <StatusDot status={running ? "running" : queued ? "queued" : "idle"} />
          <span className="tabular-nums">
            {t("shell.running", { running })}
            {queued ? ` · ${t("shell.queued", { queued })}` : ""}
          </span>
        </button>

        <span
          className="flex h-7 items-center gap-1.5 rounded border border-hairline-strong bg-canvas px-2 text-2xs text-muted"
          title={dockerOk ? `Docker ${host?.docker.server_version}` : host?.docker.error ?? t("shell.dockerMissing")}
        >
          <Boxes className="h-3 w-3" />
          <span className={dockerOk ? "text-ok" : "text-warn"}>
            {dockerOk ? t("shell.dockerOk") : host?.docker.available ? t("shell.dockerDaemonOff") : t("shell.dockerMissing")}
          </span>
        </span>

        <span
          className="flex h-7 items-center gap-1.5 rounded border border-hairline-strong bg-canvas px-2 text-2xs text-muted"
          title={browserOk ? t("shell.browserOk") : t("overview.missing")}
        >
          <FlaskConical className="h-3 w-3" />
          <span className={browserOk ? "text-ok" : "text-warn"}>{browserOk ? t("shell.browserOk") : t("shell.browserMissing")}</span>
        </span>

        {host ? (
          <span
            className="flex h-7 items-center gap-1.5 rounded border border-hairline-strong bg-canvas px-2 text-2xs text-muted"
            title={t("shell.hostTooltip", {
              cores: host.cpu_count,
              memory: Math.round(host.memory_total_mb / 1024),
              disk: Math.round(host.disk_free_mb / 1024),
            })}
          >
            <Cpu className="h-3 w-3" />
            <span className="tabular-nums">
              {t("shell.coresFree", {
                cores: host.cpu_count,
                disk: Math.round(host.disk_free_mb / 1024),
              })}
            </span>
          </span>
        ) : null}

        {latest ? (
          <span className="hidden max-w-[220px] truncate text-2xs text-faint xl:inline" title={latest.type}>
            {latest.type}
          </span>
        ) : null}

        <a
          href="https://x.com/0xkillcoin"
          target="_blank"
          rel="noopener noreferrer"
          title={t("shell.author")}
          className="flex h-7 items-center gap-1 rounded border border-hairline-strong bg-canvas px-1.5 text-2xs text-faint transition hover:text-ink"
        >
          <XIcon className="h-3 w-3" />
          <span className="hidden sm:inline">0xkillcoin</span>
        </a>

        <LanguageSwitch />
      </div>
    </header>
  );
}

export function AppShell() {
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <SystemProvider>
      <div className="flex h-screen w-screen overflow-hidden bg-canvas">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar onOpenPalette={() => setPaletteOpen(true)} />
          <main className="min-h-0 flex-1 overflow-hidden">
            <Outlet />
          </main>
        </div>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </SystemProvider>
  );
}
