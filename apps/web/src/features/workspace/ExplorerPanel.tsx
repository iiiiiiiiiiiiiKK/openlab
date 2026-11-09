import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, File, FileCode, Folder, FolderOpen } from "lucide-react";
import { api, type TreeEntry } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";
import { EmptyState } from "@/components/ui/card";
import { useT } from "@/lib/i18n";

function extIcon(name: string) {
  const lower = name.toLowerCase();
  if (/\.(ts|tsx|js|jsx|py|go|rs|rb|php|java)$/.test(lower)) return FileCode;
  return File;
}

function TreeNode({
  entry,
  depth,
  projectId,
  onOpenFile,
  selectedPath,
}: {
  entry: TreeEntry;
  depth: number;
  projectId: string;
  onOpenFile: (path: string) => void;
  selectedPath: string | null;
}) {
  const [open, setOpen] = useState(depth < 1);
  const [children, setChildren] = useState<TreeEntry[] | null>(entry.children ?? null);
  const [loading, setLoading] = useState(false);
  const t = useT();

  const toggle = async () => {
    if (entry.type === "file") {
      onOpenFile(entry.path);
      return;
    }
    const next = !open;
    setOpen(next);
    if (next && children === null) {
      setLoading(true);
      try {
        const payload = await api.projectTree(projectId, entry.path, 1);
        setChildren(payload.entries);
      } catch {
        setChildren([]);
      } finally {
        setLoading(false);
      }
    }
  };

  const Icon =
    entry.type === "dir" ? (open ? FolderOpen : Folder) : extIcon(entry.name);

  return (
    <div>
      <button
        type="button"
        onClick={() => void toggle()}
        className={cn(
          "group flex w-full items-center gap-1 rounded-sm py-[2px] pr-2 text-left text-xs transition",
          selectedPath === entry.path ? "bg-white/[0.07] text-ink" : "text-muted hover:bg-white/[0.035] hover:text-ink",
        )}
        style={{ paddingLeft: depth * 12 + 4 }}
      >
        {entry.type === "dir" ? (
          open ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-faint" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-faint" />
          )
        ) : (
          <span className="w-3" />
        )}
        <Icon className={cn("h-3.5 w-3.5 shrink-0", entry.type === "dir" ? "text-[#8fb2ff]" : "text-faint")} />
        <span className="truncate">{entry.name}</span>
        {entry.type === "file" ? (
          <span className="ml-auto shrink-0 pl-2 font-mono text-[10px] text-faint opacity-0 group-hover:opacity-100">
            {formatBytes(entry.size)}
          </span>
        ) : null}
      </button>
      {open && children ? (
        <div>
          {children.map((child) => (
            <TreeNode
              key={child.path}
              entry={child}
              depth={depth + 1}
              projectId={projectId}
              onOpenFile={onOpenFile}
              selectedPath={selectedPath}
            />
          ))}
          {children.length === 0 && !loading ? (
            <p className="py-0.5 text-[10px] text-faint" style={{ paddingLeft: (depth + 1) * 12 + 18 }}>
              {t("explorerPanel.emptyDir")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function ExplorerPanel({ projectId }: { projectId: string }) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; content: string } | null>(null);
  const t = useT();

  useEffect(() => {
    let active = true;
    setLoading(true);
    void api
      .projectTree(projectId, "", 2)
      .then((payload) => {
        if (active) {
          setEntries(payload.entries);
          setError(null);
        }
      })
      .catch((exc) => {
        if (active) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  const openFile = async (path: string) => {
    try {
      const payload = await api.projectFile(projectId, path);
      setFile({ path: payload.path, content: payload.content });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const body = useMemo(() => {
    if (loading) return <p className="px-2 py-3 text-xs text-faint">{t("explorerPanel.loading")}</p>;
    if (error) return <p className="px-2 py-3 text-xs text-danger">{error}</p>;
    if (!entries.length) return <EmptyState title={t("explorerPanel.empty")} description={t("explorerPanel.emptyDesc")} />;
    return entries.map((entry) => (
      <TreeNode
        key={entry.path}
        entry={entry}
        depth={0}
        projectId={projectId}
        onOpenFile={(path) => void openFile(path)}
        selectedPath={file?.path ?? null}
      />
    ));
  }, [entries, error, file?.path, loading, projectId]);

  return (
    <div className="flex h-full min-h-0">
      <div className="w-[260px] shrink-0 overflow-y-auto border-r border-hairline bg-surface/40 py-1.5 px-1">{body}</div>
      <div className="min-w-0 flex-1 overflow-auto bg-canvas">
        {file ? (
          <div>
            <div className="sticky top-0 flex items-center gap-2 border-b border-hairline bg-surface px-2.5 py-1.5">
              <FileCode className="h-3 w-3 text-faint" />
              <span className="truncate font-mono text-2xs text-muted">{file.path}</span>
              <span className="ml-auto text-2xs text-faint">{t("explorerPanel.lines", { count: file.content.split("\n").length })}</span>
            </div>
            <pre className="p-2.5 font-mono text-[11.5px] leading-[1.55] text-muted">
              {file.content.split("\n").map((line, index) => (
                <div key={index} className="flex hover:bg-white/[0.02]">
                  <span className="w-9 shrink-0 select-none pr-2 text-right text-faint/60">{index + 1}</span>
                  <span className="whitespace-pre-wrap break-all">{line || " "}</span>
                </div>
              ))}
            </pre>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <p className="text-xs text-faint">{t("explorerPanel.selectFile")}</p>
          </div>
        )}
      </div>
    </div>
  );
}
