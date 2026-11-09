import { useCallback, useEffect, useState } from "react";
import { Download, Film, Image as ImageIcon, Trash2 } from "lucide-react";
import { api, assetRawUrl } from "@/lib/api";
import type { AssetRow, ProjectSummary } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, EmptyState, PanelHeader } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { cn, formatBytes, formatDuration, formatNumber, timeAgo } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function AssetsPage() {
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<AssetRow | null>(null);
  const [exportDir, setExportDir] = useState("");
  const [stats, setStats] = useState<{ total: number; bytes: number; by_kind: Record<string, { count: number; bytes: number }> } | null>(null);
  const toast = useToast();
  const t = useT();

  const load = useCallback(async () => {
    try {
      const [assetPayload, projectPayload] = await Promise.all([
        api.listAssets({ project_id: projectId || undefined, kind: kind || undefined }),
        api.listProjects(),
      ]);
      setAssets(assetPayload.items);
      setStats(assetPayload.stats);
      setProjects(projectPayload.items);
    } catch {
      setAssets([]);
    }
  }, [projectId, kind]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (id: string) => {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  const remove = async (asset: AssetRow) => {
    if (!window.confirm(t("assets.confirmDelete", { name: asset.filename }))) return;
    await api.deleteAsset(asset.id);
    setSelected((current) => current.filter((item) => item !== asset.id));
    if (preview?.id === asset.id) setPreview(null);
    void load();
  };

  const exportSelected = async () => {
    if (!exportDir.trim()) {
      toast.error(t("assets.chooseExportFolder"));
      return;
    }
    try {
      const result = await api.exportAssets(selected, exportDir.trim());
      toast.success(t("assets.exported", { count: result.count }), exportDir.trim());
    } catch (exc) {
      toast.error(t("error.exportFailed"), exc instanceof Error ? exc.message : String(exc));
    }
  };

  const projectName = (id: string) => projects.find((project) => project.id === id)?.display_name || id.slice(0, 8);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-hairline px-4 py-2.5">
        <h1 className="text-sm font-semibold text-ink">{t("assets.title")}</h1>
        <span className="text-2xs text-faint">
          {t("assets.summary", { count: formatNumber(stats?.total ?? 0), size: formatBytes(stats?.bytes ?? 0) })}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Select value={projectId} onChange={(event) => setProjectId(event.target.value)} className="h-7 w-[180px] text-xs">
            <option value="">{t("assets.allProjects")}</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.display_name || project.name}
              </option>
            ))}
          </Select>
          <Select value={kind} onChange={(event) => setKind(event.target.value)} className="h-7 w-[130px] text-xs">
            <option value="">{t("assets.allKinds")}</option>
            <option value="screenshot">{t("assets.screenshots")}</option>
            <option value="video">{t("assets.videos")}</option>
            <option value="gif">{t("assets.gifs")}</option>
          </Select>
          <Input
            value={exportDir}
            onChange={(event) => setExportDir(event.target.value)}
            placeholder={t("assets.exportFolder")}
            className="h-7 w-[200px] text-xs"
          />
          <Button variant="secondary" size="sm" disabled={!selected.length} onClick={() => void exportSelected()}>
            <Download className="h-3.5 w-3.5" />
            {t("assets.export", { count: selected.length })}
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto p-4">
          {assets.length === 0 ? (
            <Card>
              <EmptyState
                icon={<ImageIcon className="h-6 w-6" />}
                title={t("assets.empty")}
                description={t("assets.emptyDesc")}
              />
            </Card>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-2.5">
              {assets.map((asset) => (
                <div
                  key={asset.id}
                  className={cn(
                    "group overflow-hidden rounded-md border bg-panel transition",
                    selected.includes(asset.id) ? "border-accent/60" : "border-hairline hover:border-hairline-strong",
                  )}
                >
                  <button type="button" onClick={() => setPreview(asset)} className="block w-full">
                    <div className="relative flex h-[118px] items-center justify-center overflow-hidden bg-[#0a0b0d]">
                      {asset.kind === "screenshot" || asset.kind === "gif" ? (
                        <img
                          src={assetRawUrl(asset.id)}
                          alt={asset.label ?? asset.filename}
                          className="h-full w-full object-cover object-top"
                          loading="lazy"
                        />
                      ) : (
                        <div className="flex flex-col items-center gap-1">
                          <Film className="h-5 w-5 text-faint" />
                          <span className="text-[10px] text-faint">{formatDuration(asset.duration_ms)}</span>
                        </div>
                      )}
                      <Badge className="absolute left-1.5 top-1.5">{asset.kind}</Badge>
                      <input
                        type="checkbox"
                        checked={selected.includes(asset.id)}
                        onChange={(event) => {
                          event.stopPropagation();
                          toggle(asset.id);
                        }}
                        onClick={(event) => event.stopPropagation()}
                        className="absolute right-1.5 top-1.5 h-3.5 w-3.5 accent-[#5b8cff]"
                      />
                    </div>
                    <div className="px-2 py-1.5 text-left">
                      <p className="truncate text-2xs text-muted">{asset.label || asset.filename}</p>
                      <p className="truncate text-[10px] text-faint">
                        {projectName(asset.project_id)} · {asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"} ·{" "}
                        {formatBytes(asset.size_bytes)}
                      </p>
                    </div>
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {preview ? (
          <div className="w-[320px] shrink-0 border-l border-hairline bg-surface/40 p-3">
            <div className="mb-2 flex items-center gap-1">
              <span className="truncate text-2xs text-muted">{preview.filename}</span>
              <Button size="icon" variant="ghost" className="ml-auto" onClick={() => setPreview(null)}>
                ✕
              </Button>
            </div>
            {preview.kind === "video" ? (
              <video src={assetRawUrl(preview.id)} controls className="w-full rounded border border-hairline" />
            ) : (
              <img src={assetRawUrl(preview.id)} alt={preview.label ?? ""} className="w-full rounded border border-hairline" />
            )}
            <dl className="mt-2 space-y-1 text-2xs">
              {[
                [t("assets.project"), projectName(preview.project_id)],
                [t("assetsPanel.kind"), preview.kind],
                [t("assetsPanel.viewport"), preview.viewport ?? "—"],
                [t("assetsPanel.size"), formatBytes(preview.size_bytes)],
                [t("assetsPanel.created"), timeAgo(preview.created_at)],
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
                onClick={() => window.open(assetRawUrl(preview.id), "_blank", "noopener")}
              >
                <Download className="h-3 w-3" />
                {t("assets.openOriginal")}
              </Button>
              {preview.kind === "video" ? (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="w-full"
                    onClick={() =>
                      void api
                        .makeGif({ asset_id: preview.id })
                        .then(() => {
                          toast.success("GIF created");
                          void load();
                        })
                        .catch((exc) => toast.error("GIF failed", String(exc)))
                    }
                  >
                    {t("assets.makeGif")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="w-full"
                    onClick={() =>
                      void api
                        .convertVideo({ asset_id: preview.id, format: "mp4", max_width: 1280 })
                        .then(() => {
                          toast.success("MP4 created");
                          void load();
                        })
                        .catch((exc) => toast.error("Conversion failed", String(exc)))
                    }
                  >
                    {t("assets.convertMp4")}
                  </Button>
                </>
              ) : null}
              <Button size="sm" variant="danger" className="w-full" onClick={() => void remove(preview)}>
                <Trash2 className="h-3 w-3" />
                {t("assets.delete")}
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      <Card className="m-4 mt-0">
        <PanelHeader title={t("assets.storage")} subtitle={t("assets.storageHint")} />
        <div className="flex flex-wrap gap-4 px-3.5 py-2.5 text-xs">
          {Object.entries(stats?.by_kind ?? {}).length === 0 ? (
            <span className="text-faint">{t("assets.nothingStored")}</span>
          ) : (
            Object.entries(stats?.by_kind ?? {}).map(([key, value]) => (
              <span key={key} className="text-muted">
                <span className="text-faint">{key}:</span> {value.count} · {formatBytes(value.bytes)}
              </span>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}
