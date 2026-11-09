import { useCallback, useEffect, useState } from "react";
import { Download, Film, Image as ImageIcon, Loader2, Sparkles, Trash2 } from "lucide-react";
import { api, assetRawUrl } from "@/lib/api";
import type { AssetRow } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { cn, formatBytes, timeAgo } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function AssetsPanel({
  projectId,
  canCapture,
  onCaptured,
}: {
  projectId: string;
  canCapture: boolean;
  onCaptured?: () => void;
}) {
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [kind, setKind] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<AssetRow | null>(null);
  const t = useT();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const payload = await api.listAssets({ project_id: projectId, kind: kind || undefined });
      setAssets(payload.items);
    } catch {
      setAssets([]);
    }
  }, [projectId, kind]);

  useEffect(() => {
    void load();
  }, [load]);

  const capture = async () => {
    setBusy(true);
    try {
      await api.captureAssets(projectId);
      toast.success(t("assetsPanel.jobQueued"), t("assetsPanel.jobQueuedDesc"));
      window.setTimeout(() => {
        void load();
        onCaptured?.();
      }, 4000);
    } catch (exc) {
      toast.error(t("error.assetsLoad"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (asset: AssetRow) => {
    if (!window.confirm(t("assets.confirmDelete", { name: asset.filename }))) return;
    await api.deleteAsset(asset.id);
    if (selected?.id === asset.id) setSelected(null);
    void load();
  };

  const makeGif = async (asset: AssetRow) => {
    try {
      await api.makeGif({ asset_id: asset.id, duration_s: 6, fps: 12, width: 960 });
      toast.success(t("assets.gifCreated"));
      void load();
    } catch (exc) {
      toast.error(t("error.gifFailed"), exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1.5 border-b border-hairline bg-surface px-2 py-1.5">
          <ImageIcon className="h-3 w-3 text-faint" />
          <span className="text-2xs text-muted">{t("assetsPanel.title")}</span>
          <div className="ml-2 flex items-center gap-0.5 rounded border border-hairline bg-canvas p-0.5">
            {[
              { id: "", label: t("assetsPanel.all") },
              { id: "screenshot", label: t("assetsPanel.screenshots") },
              { id: "video", label: t("assetsPanel.video") },
              { id: "gif", label: t("assetsPanel.gif") },
            ].map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setKind(item.id)}
                className={cn(
                  "rounded-sm px-1.5 py-0.5 text-2xs transition",
                  kind === item.id ? "bg-white/[0.07] text-ink" : "text-faint hover:text-muted",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <span className="ml-auto text-2xs text-faint">{t("common.items", { count: assets.length })}</span>
          <Button size="sm" variant="secondary" loading={busy} disabled={!canCapture} onClick={() => void capture()}>
            <Sparkles className="h-3 w-3" />
            {t("assetsPanel.captureSet")}
          </Button>
        </div>
        <div className="scroll-area min-h-0 flex-1 p-2">
          {assets.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
              <ImageIcon className="h-5 w-5 text-faint" />
              <p className="text-xs text-muted">{t("assetsPanel.empty")}</p>
              <p className="max-w-xs text-2xs text-faint">
                {t("assetsPanel.emptyDesc")}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(128px,1fr))] gap-2">
              {assets.map((asset) => (
                <button
                  key={asset.id}
                  type="button"
                  onClick={() => setSelected(asset)}
                  className={cn(
                    "group overflow-hidden rounded border bg-canvas text-left transition",
                    selected?.id === asset.id ? "border-accent/60" : "border-hairline hover:border-hairline-strong",
                  )}
                >
                  <div className="relative flex h-[78px] items-center justify-center overflow-hidden bg-[#0a0b0d]">
                    {asset.kind === "screenshot" ? (
                      <img
                        src={assetRawUrl(asset.id)}
                        alt={asset.label ?? asset.filename}
                        className="h-full w-full object-cover object-top"
                        loading="lazy"
                      />
                    ) : (
                      <Film className="h-5 w-5 text-faint" />
                    )}
                    <Badge className="absolute left-1 top-1">{asset.kind}</Badge>
                  </div>
                  <div className="px-1.5 py-1">
                    <p className="truncate text-2xs text-muted">{asset.label || asset.filename}</p>
                    <p className="truncate text-[10px] text-faint">
                      {asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"} ·{" "}
                      {formatBytes(asset.size_bytes)}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {selected ? (
        <div className="flex w-[280px] shrink-0 flex-col border-l border-hairline bg-surface/40">
          <div className="flex items-center gap-1 border-b border-hairline px-2 py-1.5">
            <span className="truncate text-2xs text-muted">{selected.filename}</span>
            <Button size="icon" variant="ghost" className="ml-auto" onClick={() => setSelected(null)}>
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
          <div className="scroll-area min-h-0 flex-1 p-2">
            {selected.kind === "screenshot" ? (
              <img src={assetRawUrl(selected.id)} alt={selected.label ?? ""} className="w-full rounded border border-hairline" />
            ) : selected.kind === "video" ? (
              <video src={assetRawUrl(selected.id)} controls className="w-full rounded border border-hairline" />
            ) : (
              <img src={assetRawUrl(selected.id)} alt={selected.label ?? ""} className="w-full rounded border border-hairline" />
            )}
            <dl className="mt-2 space-y-1 text-2xs">
              {[
                [t("assetsPanel.kind"), selected.kind],
                [t("assetsPanel.viewport"), selected.viewport ?? "—"],
                [t("assetsPanel.dimensions"), selected.width ? `${selected.width}×${selected.height}` : "—"],
                [t("assetsPanel.size"), formatBytes(selected.size_bytes)],
                [t("assetsPanel.created"), timeAgo(selected.created_at)],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between gap-2">
                  <dt className="text-faint">{label}</dt>
                  <dd className="truncate text-muted">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-2 space-y-1">
              <Button
                size="sm"
                variant="secondary"
                className="w-full"
                onClick={() => window.open(assetRawUrl(selected.id), "_blank", "noopener")}
              >
                <Download className="h-3 w-3" />
                {t("assetsPanel.openOriginal")}
              </Button>
              {selected.kind === "video" ? (
                <Button size="sm" variant="secondary" className="w-full" onClick={() => void makeGif(selected)}>
                  <Loader2 className="h-3 w-3" />
                  {t("assetsPanel.makeGif")}
                </Button>
              ) : null}
              <Button size="sm" variant="danger" className="w-full" onClick={() => void remove(selected)}>
                <Trash2 className="h-3 w-3" />
                {t("assetsPanel.deleteAsset")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
