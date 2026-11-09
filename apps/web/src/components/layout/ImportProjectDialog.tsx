import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Github, FolderOpen, Upload, Loader2, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Field } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

type Tab = "github" | "zip" | "local";

export function ImportProjectDialog({
  open,
  onClose,
  initialTab = "github",
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  initialTab?: Tab;
  onImported?: (projectId: string) => void;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  const t = useT();

  const reset = () => {
    setError(null);
    setBusy(false);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      let projectId = "";
      if (tab === "github") {
        if (!url.trim()) throw new Error(t("import.errUrl"));
        const result = await api.importGithub(url.trim(), token.trim() || undefined);
        projectId = result.project_id;
        toast.success(t("import.okGithub"), `${result.name} · ${t("import.filesAnalysed", { count: result.files })}`);
      } else if (tab === "zip") {
        if (!file) throw new Error(t("import.errFile"));
        const result = await api.importZip(file, displayName.trim() || undefined);
        projectId = result.project_id;
        toast.success(t("import.okZip"), `${result.name} · ${t("import.filesAnalysed", { count: result.files })}`);
      } else {
        if (!localPath.trim()) throw new Error(t("import.errPath"));
        const result = await api.importLocal(localPath.trim(), displayName.trim() || undefined);
        projectId = result.project_id;
        toast.success(t("import.okFolder"), `${result.name} · ${t("import.filesAnalysed", { count: result.files })}`);
      }
      onImported?.(projectId);
      onClose();
      reset();
      if (projectId) navigate(`/projects/${projectId}`);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      toast.error(t("import.failed"), message);
    } finally {
      setBusy(false);
    }
  };

  const tabs: Array<{ id: Tab; label: string; icon: typeof Github }> = [
    { id: "github", label: t("import.tabGithub"), icon: Github },
    { id: "zip", label: t("import.tabZip"), icon: Upload },
    { id: "local", label: t("import.tabLocal"), icon: FolderOpen },
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("import.title")}
      description={t("import.description")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} loading={busy}>
            {busy ? t("import.importing") : t("import.submit")}
          </Button>
        </>
      }
    >
      <div className="mb-3 flex gap-1 rounded border border-hairline bg-canvas p-0.5">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              setTab(item.id);
              setError(null);
            }}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 rounded-sm px-2 py-1.5 text-xs transition",
              tab === item.id ? "bg-white/[0.07] text-ink" : "text-faint hover:text-muted",
            )}
          >
            <item.icon className="h-3.5 w-3.5" />
            {item.label}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {tab === "github" ? (
          <>
            <Field label={t("import.url")} hint={t("import.urlHint")}>
              <Input
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://github.com/owner/repo"
                autoFocus
              />
            </Field>
            <Field label={t("import.token")} hint={t("import.tokenHint")}>
              <Input
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="ghp_…"
              />
            </Field>
            <p className="text-2xs leading-relaxed text-faint">
              {t("import.tokenNote")}
            </p>
          </>
        ) : null}

        {tab === "zip" ? (
          <>
            <Field label={t("import.zipFile")}>
              <input
                type="file"
                accept=".zip"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="w-full rounded border border-hairline-strong bg-canvas px-2 py-1.5 text-xs text-muted file:mr-2 file:rounded file:border-0 file:bg-elevated file:px-2 file:py-1 file:text-xs file:text-ink"
              />
            </Field>
            <Field label={t("import.displayName")}>
              <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder={t("import.displayNamePlaceholder")} />
            </Field>
          </>
        ) : null}

        {tab === "local" ? (
          <>
            <Field label={t("import.folderPath")} hint={t("import.folderHint")}>
              <Input
                value={localPath}
                onChange={(event) => setLocalPath(event.target.value)}
                placeholder="C:\\code\\my-project"
                autoFocus
              />
            </Field>
            <Field label={t("import.displayName")}>
              <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder={t("import.displayNamePlaceholder")} />
            </Field>
            <p className="text-2xs leading-relaxed text-faint">
              {t("import.folderNote")}
            </p>
          </>
        ) : null}

        {error ? (
          <div className="flex items-start gap-2 rounded border border-danger/40 bg-danger/10 px-2.5 py-2 text-xs text-danger">
            <AlertTriangle className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        ) : null}

        {busy ? (
          <div className="flex items-center gap-2 text-xs text-faint">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t("import.busy")}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
