import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export type BadgeTone = "neutral" | "ok" | "warn" | "danger" | "info" | "accent";

const tones: Record<BadgeTone, string> = {
  neutral: "border-hairline-strong bg-elevated text-muted",
  ok: "border-ok/35 bg-ok/10 text-ok",
  warn: "border-warn/35 bg-warn/10 text-warn",
  danger: "border-danger/35 bg-danger/10 text-danger",
  info: "border-info/35 bg-info/10 text-info",
  accent: "border-accent/40 bg-accent/12 text-[#8fb2ff]",
};

export function Badge({
  children,
  tone = "neutral",
  className,
  dot,
}: {
  children: React.ReactNode;
  tone?: BadgeTone;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-[1px] text-2xs font-medium",
        tones[tone],
        className,
      )}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, BadgeTone> = {
  RUNNING: "ok",
  VERIFIED: "ok",
  succeeded: "ok",
  ready: "ok",
  PENDING: "neutral",
  QUEUED: "neutral",
  queued: "neutral",
  STOPPED: "neutral",
  UNVERIFIED: "neutral",
  ANALYZING: "info",
  PLANNING: "info",
  PREPARING: "info",
  INSTALLING: "info",
  BUILDING: "info",
  STARTING: "info",
  HEALTH_CHECK: "info",
  running: "info",
  PARTIALLY_VERIFIED: "warn",
  paused: "warn",
  WAITING_FOR_USER: "warn",
  waiting_for_user: "warn",
  FAILED: "danger",
  failed: "danger",
  BLOCKED: "danger",
  CANCELLED: "danger",
  cancelled: "danger",
};

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  const t = useT();
  const value = String(status ?? "UNKNOWN");
  const tone = STATUS_TONES[value] ?? "neutral";
  const live = value === "RUNNING" || value === "running";
  // Unknown statuses fall back to the raw value so nothing is hidden.
  const label = t(`status.${value}`);
  return (
    <Badge tone={tone} className={className} dot={live}>
      {label === `status.${value}` ? value.replace(/_/g, " ").toLowerCase() : label}
    </Badge>
  );
}

export function StatusDot({ status }: { status: string | null | undefined }) {
  const value = String(status ?? "");
  const color =
    STATUS_TONES[value] === "ok"
      ? "bg-ok"
      : STATUS_TONES[value] === "danger"
        ? "bg-danger"
        : STATUS_TONES[value] === "warn"
          ? "bg-warn"
          : STATUS_TONES[value] === "info"
            ? "bg-info animate-pulse-dot"
            : "bg-faint";
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", color)} />;
}
