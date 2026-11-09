import { useCallback, useEffect, useState } from "react";
import { HardDriveDownload, History, RotateCcw, Save } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { formatDateTime } from "@/lib/utils";
import { useT } from "@/lib/i18n";

interface SnapshotRow {
  id: string;
  label: string | null;
  compose_path: string | null;
  env_names: string[];
  created_at: string | null;
}

export function SnapshotPanel({ projectId }: { projectId: string }) {
  const [snapshots, setSnapshots] = useState<SnapshotRow[]>([]);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const t = useT();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const payload = await api.snapshots(projectId);
      setSnapshots(payload.items);
    } catch {
      setSnapshots([]);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setBusy("create");
    try {
      await api.createSnapshot(projectId, label.trim() || undefined);
      setLabel("");
      toast.success(t("snapshot.saved"), t("snapshot.savedDesc"));
      await load();
    } catch (exc) {
      toast.error(t("error.snapshotFailed"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const restore = async (snapshot: SnapshotRow) => {
    if (
      !window.confirm(
        t("snapshot.confirmRestore", { name: snapshot.label || snapshot.id.slice(0, 8) }),
      )
    ) {
      return;
    }
    setBusy(snapshot.id);
    try {
      const result = await api.restoreSnapshot(projectId, snapshot.id);
      toast.success(t("snapshot.restored"), result.next_step);
    } catch (exc) {
      toast.error(t("error.restoreFailed"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex h-full flex-col bg-canvas">
      <div className="flex items-center gap-2 border-b border-hairline bg-surface px-2.5 py-1.5">
        <History className="h-3 w-3 text-faint" />
        <span className="text-2xs text-muted">{t("snapshot.title")}</span>
        <span className="text-2xs text-faint">{t("snapshot.subtitle")}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder={t("snapshot.labelPlaceholder")}
            className="h-6 w-[160px] text-2xs"
          />
          <Button size="sm" variant="secondary" loading={busy === "create"} onClick={() => void create()}>
            <Save className="h-3 w-3" />
            {t("snapshot.save")}
          </Button>
        </div>
      </div>

      <div className="scroll-area min-h-0 flex-1 p-2">
        {snapshots.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <HardDriveDownload className="h-5 w-5 text-faint" />
            <p className="text-xs text-muted">{t("snapshot.empty")}</p>
            <p className="max-w-sm text-2xs text-faint">
              {t("snapshot.emptyDesc")}
            </p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {snapshots.map((snapshot) => (
              <div
                key={snapshot.id}
                className="flex items-start gap-2 rounded border border-hairline bg-panel px-2.5 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="truncate text-xs text-ink">{snapshot.label || snapshot.id.slice(0, 8)}</p>
                    <Badge>{formatDateTime(snapshot.created_at)}</Badge>
                    {snapshot.env_names.length ? (
                      <Badge tone="accent">{t("snapshot.envVars", { count: snapshot.env_names.length })}</Badge>
                    ) : null}
                  </div>
                  <p className="truncate text-2xs text-faint">
                    {snapshot.compose_path ? t("snapshot.composeCaptured") : t("snapshot.noCompose")} ·{" "}
                    {snapshot.env_names.join(", ") || t("snapshot.noEnv")}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={busy === snapshot.id}
                  onClick={() => void restore(snapshot)}
                >
                  <RotateCcw className="h-3 w-3" />
                  {t("snapshot.restore")}
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
