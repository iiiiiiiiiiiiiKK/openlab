/**
 * Formatting helpers shared by the desktop shell and any embedded view.
 *
 * The React app has its own copy in `apps/web/src/lib/utils.ts` because it needs
 * Tailwind-aware helpers too; these are the deliberately framework-free subset.
 */

export function humanBytes(value: number | null | undefined): string {
  const size = Number(value ?? 0);
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let current = size;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(current >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function humanDuration(ms: number | null | undefined): string {
  const total = Math.max(0, Math.round(Number(ms ?? 0) / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${total % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function relativeTime(value: string | null | undefined, now: number = Date.now()): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = now - then;
  if (diff < 45_000) return "just now";
  const minutes = Math.round(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(value).toISOString().slice(0, 10);
}

export function clampProgress(value: number | null | undefined): number {
  const number = Number(value ?? 0);
  if (Number.isNaN(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

/** Build the local preview URL for a project container port. */
export function localUrl(port: number | null | undefined): string | null {
  return port ? `http://127.0.0.1:${port}` : null;
}
