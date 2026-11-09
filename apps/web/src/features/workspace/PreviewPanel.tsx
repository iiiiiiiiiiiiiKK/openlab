import { useCallback, useEffect, useRef, useState } from "react";
import { Globe, RotateCw, Smartphone, Monitor, Tablet, Camera, ExternalLink, Maximize2 } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

const VIEWPORTS = [
  { id: "desktop", labelKey: "preview.desktop", icon: Monitor, width: 1440 },
  { id: "tablet", labelKey: "preview.tablet", icon: Tablet, width: 834 },
  { id: "mobile", labelKey: "preview.mobile", icon: Smartphone, width: 390 },
];

export function PreviewPanel({
  projectId,
  url,
  onCaptured,
}: {
  projectId: string;
  url: string | null;
  onCaptured?: () => void;
}) {
  const [viewport, setViewport] = useState("desktop");
  const [reloadKey, setReloadKey] = useState(0);
  const [capturing, setCapturing] = useState(false);
  const [status, setStatus] = useState<{ title?: string; code?: string } | null>(null);
  const [captureUrl, setCaptureUrl] = useState<string | null>(null);
  const t = useT();
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const frameWidth = VIEWPORTS.find((item) => item.id === viewport)?.width ?? 1440;

  const capture = useCallback(async () => {
    setCapturing(true);
    try {
      const result = await api.preview(projectId, { viewport, full_page: true });
      const assetId = (result.snapshot as { asset_id?: string }).asset_id;
      if (assetId) setCaptureUrl(`/api/assets/${assetId}/raw?ts=${Date.now()}`);
      setStatus({ title: result.title });
      onCaptured?.();
    } catch (exc) {
      setStatus({ code: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setCapturing(false);
    }
  }, [onCaptured, projectId, viewport]);

  useEffect(() => {
    setCaptureUrl(null);
  }, [url]);

  if (!url) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <Globe className="h-6 w-6 text-faint" />
        <p className="text-sm font-medium text-ink">{t("preview.noPreview")}</p>
        <p className="max-w-xs text-xs leading-relaxed text-faint">
          {t("preview.noPreviewDesc")}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1.5 border-b border-hairline bg-surface px-2 py-1.5">
        <div className="flex items-center gap-0.5 rounded border border-hairline bg-canvas p-0.5">
          {VIEWPORTS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setViewport(item.id)}
              className={cn(
                "flex items-center gap-1 rounded-sm px-1.5 py-1 text-2xs transition",
                viewport === item.id ? "bg-white/[0.07] text-ink" : "text-faint hover:text-muted",
              )}
              title={t(item.labelKey)}
            >
              <item.icon className="h-3 w-3" />
            </button>
          ))}
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded border border-hairline bg-canvas px-2 py-1">
          <Globe className="h-3 w-3 shrink-0 text-faint" />
          <span className="truncate font-mono text-2xs text-muted">{url}</span>
          <Badge className="ml-auto shrink-0">{`${frameWidth}px`}</Badge>
        </div>
        <Button size="icon" variant="ghost" onClick={() => setReloadKey((key) => key + 1)} title={t("preview.reloadFrame")}>
          <RotateCw className="h-3.5 w-3.5" />
        </Button>
        <Button size="icon" variant="ghost" onClick={() => window.open(url, "_blank", "noopener")} title={t("preview.openTab")}>
          <ExternalLink className="h-3.5 w-3.5" />
        </Button>
        <Button size="sm" variant="secondary" loading={capturing} onClick={() => void capture()}>
          <Camera className="h-3.5 w-3.5" />
          {t("preview.screenshot")}
        </Button>
      </div>

      {status?.code ? (
        <div className="border-b border-danger/30 bg-danger/10 px-2.5 py-1.5 text-2xs text-danger">{status.code}</div>
      ) : null}

      <div className="relative min-h-0 flex-1 overflow-auto bg-[#0a0b0d] hairline-grid">
        <div className="flex min-h-full justify-center p-3">
          <div className="h-full w-full max-w-full overflow-hidden rounded border border-hairline bg-white">
            <iframe
              key={`${reloadKey}-${viewport}`}
              ref={iframeRef}
              src={url}
              title={t("preview.frameTitle")}
              className="h-full min-h-[520px] w-full origin-top border-0 bg-white"
              style={{ width: frameWidth, maxWidth: "100%" }}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
            />
          </div>
        </div>
      </div>

      {captureUrl ? (
        <div className="border-t border-hairline bg-surface px-2.5 py-2">
          <div className="flex items-center gap-2">
            <Camera className="h-3 w-3 text-ok" />
            <span className="text-2xs text-muted">{t("preview.captured")}</span>
            <button
              type="button"
              onClick={() => window.open(captureUrl, "_blank", "noopener")}
              className="ml-auto flex items-center gap-1 text-2xs text-[#9dbcff] hover:underline"
            >
              <Maximize2 className="h-3 w-3" />
              {t("common.view")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
