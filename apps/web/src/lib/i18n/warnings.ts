/**
 * Translate the warning strings produced by the backend.
 *
 * The backend emits warnings as English sentences (they also end up in logs and
 * reports, where English is the right choice). The UI maps the known ones to the
 * active locale and falls back to the original text so a new warning is never
 * hidden, only left untranslated.
 */

import type { Locale } from "./index";

const KNOWN_WARNINGS: Array<{ match: RegExp; key: string }> = [
  { match: /docker is not installed/i, key: "docker.warn.notInstalled" },
  { match: /docker daemon is (stopped|not running)/i, key: "docker.warn.daemonStopped" },
  { match: /docker (cli|command) (not found|is missing)/i, key: "docker.warn.notInstalled" },
  { match: /playwright chromium is not installed/i, key: "docker.warn.chromium" },
  { match: /ffmpeg not found/i, key: "docker.warn.ffmpeg" },
  { match: /less than 5gb free disk space/i, key: "docker.warn.disk" },
  { match: /^start docker desktop/i, key: "docker.hint.start" },
  { match: /install docker desktop for windows/i, key: "docker.hint.installWindows" },
  { match: /install docker desktop for macos/i, key: "docker.hint.installMac" },
  { match: /install docker engine/i, key: "docker.hint.installLinux" },
];

export function translateWarning(
  locale: Locale,
  message: string,
  translateFn: (key: string) => string,
): string {
  if (locale === "en") return message;
  const hit = KNOWN_WARNINGS.find((entry) => entry.match.test(message));
  if (!hit) return message;
  const translated = translateFn(hit.key);
  return translated === hit.key ? message : translated;
}
