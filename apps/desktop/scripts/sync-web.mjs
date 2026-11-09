// Copy the built web UI into the Tauri bundle directory.
//
// `tauri.conf.json` points `frontendDist` at `apps/desktop/web`, which must contain
// the production build. The backend serves the same files in browser mode, so this
// script only mirrors them — it never rewrites them.

import { cp, mkdir, rm, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(here, "..");
const source = path.resolve(desktopDir, "..", "web", "dist");
const target = path.resolve(desktopDir, "web");

async function main() {
  if (!existsSync(source)) {
    console.error(
      `[sync-web] ${source} does not exist.\n` +
        "           Build the web UI first:  npm --prefix apps/web run build",
    );
    process.exit(1);
  }
  const info = await stat(source);
  if (!info.isDirectory()) {
    console.error(`[sync-web] ${source} is not a directory`);
    process.exit(1);
  }

  await rm(target, { recursive: true, force: true });
  await mkdir(target, { recursive: true });
  await cp(source, target, { recursive: true });
  console.log(`[sync-web] copied ${path.relative(desktopDir, source)} -> ${path.relative(desktopDir, target)}`);
}

main().catch((error) => {
  console.error("[sync-web] failed:", error);
  process.exit(1);
});
