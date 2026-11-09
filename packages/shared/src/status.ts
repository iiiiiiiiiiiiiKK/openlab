/**
 * Status constants shared by the UI.
 *
 * These mirror `engine/core/states.py` exactly. Keeping the vocabulary in one place
 * means a badge, a filter and a report never disagree about what "running" means.
 */

export const DEPLOY_STATUS_ORDER = [
  "PENDING",
  "ANALYZING",
  "PLANNING",
  "PREPARING",
  "INSTALLING",
  "BUILDING",
  "STARTING",
  "HEALTH_CHECK",
  "RUNNING",
] as const;

export const DEPLOY_TERMINAL_STATUSES = [
  "RUNNING",
  "FAILED",
  "WAITING_FOR_USER",
  "STOPPED",
  "CANCELLED",
] as const;

export const TASK_TERMINAL_STATUSES = ["succeeded", "failed", "cancelled"] as const;

export const VERIFICATION_STATUSES = [
  "VERIFIED",
  "PARTIALLY_VERIFIED",
  "UNVERIFIED",
  "FAILED",
  "BLOCKED",
] as const;

export const EXPLORATION_MODES = ["quick", "deep", "custom"] as const;

export const VIEWPORT_PRESETS = ["desktop", "tablet", "mobile", "square"] as const;

export const ASPECT_RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "2:1"] as const;

export function isDeployBusy(status: string | null | undefined): boolean {
  return DEPLOY_STATUS_ORDER.includes((status ?? "") as (typeof DEPLOY_STATUS_ORDER)[number]) &&
    status !== "RUNNING";
}

export function isDeployLive(status: string | null | undefined): boolean {
  return status === "RUNNING";
}

export function needsAttention(status: string | null | undefined): boolean {
  return status === "FAILED" || status === "WAITING_FOR_USER";
}
