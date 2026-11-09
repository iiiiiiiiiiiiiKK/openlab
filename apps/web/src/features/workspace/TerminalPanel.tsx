import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Terminal as TerminalIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { TaskLogRow } from "@openlab/types";
import { useSystem } from "@/lib/system";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

const LEVEL_COLOR: Record<string, string> = {
  info: "text-muted",
  warn: "text-warn",
  error: "text-danger",
};

export function TerminalPanel({
  projectId,
  canExec,
}: {
  projectId: string;
  canExec: boolean;
}) {
  const [lines, setLines] = useState<TaskLogRow[]>([]);
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { subscribe } = useSystem();
  const t = useT();

  useEffect(() => {
    let active = true;
    void api
      .terminalHistory(projectId)
      .then((payload) => {
        if (active) setLines(payload.lines);
      })
      .catch(() => undefined);
    const unsubscribe = subscribe((event) => {
      if (event.type !== "task.log") return;
      const payload = event.payload as { project_id?: string; seq?: number; message?: string; stream?: string; level?: string } | undefined;
      if (!payload || payload.project_id !== projectId) return;
      setLines((current) => [
        ...current.slice(-999),
        {
          seq: payload.seq ?? current.length,
          stream: payload.stream ?? "stdout",
          level: payload.level ?? "info",
          message: payload.message ?? "",
          at: event.at ?? null,
        },
      ]);
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [projectId, subscribe]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [lines.length]);

  const runCommand = async () => {
    const command = input.trim();
    if (!command || busy) return;
    setHistory((current) => [...current, command]);
    setHistoryIndex(-1);
    setInput("");
    setBusy(true);
    setLines((current) => [
      ...current,
      { seq: current.length, stream: "stdin", level: "info", message: `$ ${command}`, at: new Date().toISOString() },
    ]);
    try {
      const result = await api.terminalExec(projectId, command);
      const appended: TaskLogRow[] = [];
      if (result.stdout) {
        result.stdout.split("\n").forEach((line) =>
          appended.push({ seq: 0, stream: "stdout", level: "info", message: line, at: null }),
        );
      }
      if (result.stderr) {
        result.stderr.split("\n").forEach((line) =>
          appended.push({ seq: 0, stream: "stderr", level: "error", message: line, at: null }),
        );
      }
      appended.push({
        seq: 0,
        stream: "meta",
        level: result.ok ? "info" : "warn",
        message: `[exit ${result.code} · ${result.duration_ms}ms]`,
        at: null,
      });
      setLines((current) => [...current, ...appended]);
    } catch (exc) {
      setLines((current) => [
        ...current,
        {
          seq: 0,
          stream: "stderr",
          level: "error",
          message: exc instanceof Error ? exc.message : String(exc),
          at: null,
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void runCommand();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!history.length) return;
      const next = historyIndex < 0 ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(next);
      setInput(history[next] ?? "");
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!history.length || historyIndex < 0) return;
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(-1);
        setInput("");
      } else {
        setHistoryIndex(next);
        setInput(history[next] ?? "");
      }
    }
  };

  const rendered = useMemo(() => lines.slice(-600), [lines]);

  return (
    <div className="flex h-full flex-col bg-[#08090b]">
      <div className="flex items-center gap-1.5 border-b border-hairline bg-surface px-2.5 py-1.5">
        <TerminalIcon className="h-3 w-3 text-faint" />
        <span className="text-2xs text-muted">{t("terminal.title")}</span>
        <span className="text-2xs text-faint">{t("terminal.subtitle")}</span>
        <span className="ml-auto text-2xs text-faint">{t("common.lines", { count: lines.length })}</span>
      </div>
      <div className="scroll-area min-h-0 flex-1 px-2.5 py-2 term">
        {rendered.length === 0 ? (
          <p className="text-faint">
            {t("terminal.empty")}
          </p>
        ) : (
          rendered.map((line, index) => (
            <div
              key={`${line.seq}-${index}`}
              className={cn(
                "whitespace-pre-wrap break-words",
                line.stream === "stdin" && "text-[#9dbcff]",
                line.stream === "meta" && "text-faint",
                LEVEL_COLOR[line.level] ?? "text-muted",
              )}
            >
              {line.message}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
      <div className="flex items-center gap-1.5 border-t border-hairline bg-surface px-2 py-1.5">
        <ChevronRight className={cn("h-3.5 w-3.5", canExec ? "text-ok" : "text-faint")} />
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={!canExec || busy}
          placeholder={
            canExec ? t("terminal.placeholderReady") : t("terminal.placeholderIdle")
          }
          className="h-6 flex-1 bg-transparent font-mono text-xs text-ink placeholder:text-faint focus:outline-none disabled:cursor-not-allowed"
        />
        {busy ? <span className="text-2xs text-faint">{t("terminal.running")}</span> : null}
      </div>
    </div>
  );
}
