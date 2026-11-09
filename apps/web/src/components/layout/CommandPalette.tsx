import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, FolderKanban, Rocket, Search, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@openlab/types";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const navigate = useNavigate();
  const t = useT();

  useEffect(() => {
    if (!open) return;
    setQuery("");
    void api.listProjects({ sort: "updated" }).then((payload) => setProjects(payload.items)).catch(() => setProjects([]));
  }, [open]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = needle
      ? projects.filter(
          (project) =>
            project.name.toLowerCase().includes(needle) ||
            (project.display_name ?? "").toLowerCase().includes(needle) ||
            (project.source_url ?? "").toLowerCase().includes(needle),
        )
      : projects;
    return list.slice(0, 6);
  }, [projects, query]);

  if (!open) return null;

  const go = (path: string) => {
    navigate(path);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-6 backdrop-blur-sm" onClick={onClose}>
      <div
        className="mt-[10vh] w-full max-w-xl animate-fade-in overflow-hidden rounded-lg border border-hairline-strong bg-panel shadow-pop"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-hairline px-3 py-2.5">
          <Search className="h-4 w-4 text-faint" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("palette.placeholder")}
            className="h-6 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
          <span className="kbd">esc</span>
        </div>
        <div className="max-h-[320px] overflow-y-auto p-1.5">
          <p className="px-2 py-1 text-2xs uppercase tracking-wide text-faint">{t("palette.actions")}</p>
          {[
            { label: t("palette.import"), path: "/projects?import=github", icon: Rocket },
            { label: t("palette.openExplorer"), path: "/explorer", icon: Sparkles },
            { label: t("palette.allAssets"), path: "/assets", icon: FolderKanban },
            { label: t("palette.providers"), path: "/providers", icon: Sparkles },
          ].map((action) => (
            <button
              key={action.path}
              type="button"
              onClick={() => go(action.path)}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-muted transition hover:bg-white/[0.05] hover:text-ink"
            >
              <action.icon className="h-3.5 w-3.5 text-faint" />
              {action.label}
              <ArrowRight className="ml-auto h-3 w-3 text-faint" />
            </button>
          ))}
          <p className="mt-1 px-2 py-1 text-2xs uppercase tracking-wide text-faint">{t("palette.projects")}</p>
          {filtered.length === 0 ? (
            <p className="px-2 py-2 text-xs text-faint">{t("palette.noProjects")}</p>
          ) : (
            filtered.map((project) => (
              <button
                key={project.id}
                type="button"
                onClick={() => go(`/projects/${project.id}`)}
                className={cn(
                  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-muted transition",
                  "hover:bg-white/[0.05] hover:text-ink",
                )}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-hairline-strong" />
                <span className="truncate">{project.display_name || project.name}</span>
                <span className="ml-auto truncate text-2xs text-faint">{project.deploy_status}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
