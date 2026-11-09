import { useEffect, useRef, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

const SOURCES = [
  { id: "deploy", label: "deploy.log" },
  { id: "browser", label: "browser.log" },
  { id: "host-install", label: "host-install.log" },
  { id: "host-runtime", label: "host-runtime.log" },
];

export function LogsPanel({ projectId }: { projectId: string }) {
  const [source, setSource] = useState("deploy");
  const [lines, setLines] = useState<string[]>([]);
  const [exists, setExists] = useState(true);
  const [follow, setFollow] = useState(true);
  const [loading, setLoading] = useState(false);
  const t = useT();
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = async (name = source) => {
    setLoading(true);
    try {
      const payload = await api.projectLogs(projectId, name, 800);
      setLines(payload.lines);
      setExists(payload.exists);
    } catch {
      setLines([]);
      setExists(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(source);
    const interval = window.setInterval(() => void load(source), 5000);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, source]);

  useEffect(() => {
    if (follow) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [lines, follow]);

  const color = (line: string) => {
    if (/error|failed|exception|traceback/i.test(line)) return "text-danger";
    if (/warn/i.test(line)) return "text-warn";
    if (line.startsWith("$")) return "text-[#9dbcff]";
    if (/^\[exit/.test(line)) return "text-faint";
    return "text-muted";
  };

  return (
    <div className="flex h-full flex-col bg-canvas">
      <div className="flex items-center gap-1.5 border-b border-hairline bg-surface px-2 py-1.5">
        <ScrollText className="h-3 w-3 text-faint" />
        <Select value={source} onChange={(event) => setSource(event.target.value)} className="h-6 w-[150px] text-2xs">
          {SOURCES.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </Select>
        <label className="ml-1 flex items-center gap-1 text-2xs text-faint">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
            className="h-3 w-3 accent-[#5b8cff]"
          />
          {t("logs.follow")}
        </label>
        <span className="ml-auto text-2xs text-faint">{t("common.lines", { count: lines.length })}</span>
        <Button size="icon" variant="ghost" loading={loading} onClick={() => void load(source)} title={t("common.reload")}>
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="scroll-area min-h-0 flex-1 px-2.5 py-2 term">
        {!exists ? (
          <p className="text-faint">
            {t("logs.empty", { source })}
          </p>
        ) : lines.length === 0 ? (
          <p className="text-faint">{t("logs.fileEmpty")}</p>
        ) : (
          lines.map((line, index) => (
            <div key={index} className={cn("whitespace-pre-wrap break-words", color(line))}>
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
