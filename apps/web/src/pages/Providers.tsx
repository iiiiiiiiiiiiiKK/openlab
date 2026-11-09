import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FlaskConical, Plus, Trash2, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { ProviderRow, UsageSummary } from "@openlab/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, EmptyState, Metric, PanelHeader } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { formatNumber, timeAgo } from "@/lib/utils";
import { useT } from "@/lib/i18n";

const KINDS = [
  { id: "openai", label: "OpenAI", format: "openai-chat" },
  { id: "anthropic", label: "Anthropic", format: "anthropic-messages" },
  { id: "gemini", label: "Google Gemini", format: "gemini-generate" },
  { id: "deepseek", label: "DeepSeek", format: "openai-chat" },
  { id: "compatible", label: "OpenAI compatible", format: "openai-chat" },
];

const EMPTY = {
  name: "",
  kind: "openai",
  base_url: "",
  api_key: "",
  model: "",
  cheap_model: "",
  price_in_per_mtok: 0,
  price_out_per_mtok: 0,
  is_default: false,
};

export function ProvidersPage() {
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const toast = useToast();
  const t = useT();

  const load = useCallback(async () => {
    try {
      const [providerPayload, usagePayload] = await Promise.all([
        api.listProviders(),
        api.usageSummary({ days: 30 }),
      ]);
      setProviders(providerPayload.items);
      setUsage(usagePayload);
    } catch (exc) {
      toast.error(t("error.providerLoad"), exc instanceof Error ? exc.message : String(exc));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    if (!form.name.trim()) {
      toast.error(t("providers.enterName"));
      return;
    }
    setBusy(true);
    try {
      const kindFormat = KINDS.find((item) => item.id === form.kind)?.format ?? "openai-chat";
      await api.createProvider({
        name: form.name.trim(),
        kind: form.kind,
        base_url: form.base_url.trim() || undefined,
        api_key: form.api_key.trim() || undefined,
        model: form.model.trim() || undefined,
        cheap_model: form.cheap_model.trim() || undefined,
        api_format: kindFormat,
        price_in_per_mtok: Number(form.price_in_per_mtok) || 0,
        price_out_per_mtok: Number(form.price_out_per_mtok) || 0,
        is_default: form.is_default,
      });
      toast.success(t("providers.saved"), t("providers.savedDesc"));
      setOpen(false);
      setForm({ ...EMPTY });
      await load();
    } catch (exc) {
      toast.error(t("error.providerSave"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const test = async (provider: ProviderRow) => {
    setTesting(provider.id);
    try {
      const result = await api.testProvider(provider.id);
      if (result.ok) toast.success(t("providers.connectionOk"), `${result.model} · ${result.latency_ms}ms`);
      else toast.error(t("providers.connectionFailed"), result.error ?? t("common.unknown"));
      await load();
    } catch (exc) {
      toast.error(t("error.providerTest"), exc instanceof Error ? exc.message : String(exc));
    } finally {
      setTesting(null);
    }
  };

  const remove = async (provider: ProviderRow) => {
    if (!window.confirm(t("providers.confirmDelete", { name: provider.name }))) return;
    await api.deleteProvider(provider.id);
    await load();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1100px] px-5 py-4">
        <div className="mb-3 flex items-end justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-ink">{t("providers.title")}</h1>
            <p className="text-xs text-faint">
              {t("providers.subtitle")}
            </p>
          </div>
          <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
            {t("providers.add")}
          </Button>
        </div>

        <Card className="mb-3">
          <div className="grid grid-cols-2 divide-x divide-hairline md:grid-cols-4">
            <Metric label={t("providers.requests30d")} value={formatNumber(usage?.requests ?? 0)} />
            <Metric label={t("providers.tokens30d")} value={formatNumber(usage?.tokens ?? 0)} />
            <Metric label={t("providers.estCost")} value={`$${(usage?.cost_usd ?? 0).toFixed(4)}`} hint={t("providers.estimate")} />
            <Metric
              label={t("providers.unknownUsage")}
              value={String(usage?.records_with_unknown_usage ?? 0)}
              hint={t("providers.unknownUsageHint")}
              tone={usage?.records_with_unknown_usage ? "warn" : "default"}
            />
          </div>
        </Card>

        <Card>
          <PanelHeader
            title={t("providers.list", { count: providers.length })}
            subtitle={t("providers.listHint")}
          />
          {providers.length === 0 ? (
            <EmptyState
              icon={<FlaskConical className="h-6 w-6" />}
              title={t("providers.empty")}
              description={t("providers.emptyDesc")}
              action={
                <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
                  <Plus className="h-3.5 w-3.5" />
                  {t("providers.addOne")}
                </Button>
              }
            />
          ) : (
            <div className="divide-y divide-hairline">
              {providers.map((provider) => (
                <div key={provider.id} className="flex items-center gap-3 px-3.5 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <p className="truncate text-sm text-ink">{provider.name}</p>
                      <Badge>{provider.kind}</Badge>
                      {provider.is_default ? <Badge tone="accent">{t("providers.default")}</Badge> : null}
                      {!provider.enabled ? <Badge tone="warn">{t("providers.disabled")}</Badge> : null}
                    </div>
                    <p className="truncate text-2xs text-faint">
                      {provider.base_url || t("providers.defaultEndpoint")} · {provider.model}
                      {provider.has_api_key ? ` · ${provider.api_key_masked}` : ` · ${t("providers.noKey")}`}
                    </p>
                    {provider.last_check_status ? (
                      <p className="flex items-center gap-1 text-2xs">
                        {provider.last_check_status === "ok" ? (
                          <CheckCircle2 className="h-3 w-3 text-ok" />
                        ) : (
                          <XCircle className="h-3 w-3 text-danger" />
                        )}
                        <span className={provider.last_check_status === "ok" ? "text-ok" : "text-danger"}>
                          {provider.last_check_status}
                        </span>
                        <span className="text-faint">{timeAgo(provider.last_checked_at)}</span>
                        {provider.last_check_error ? (
                          <span className="truncate text-faint">· {provider.last_check_error}</span>
                        ) : null}
                      </p>
                    ) : null}
                  </div>
                  <Button size="sm" variant="secondary" loading={testing === provider.id} onClick={() => void test(provider)}>
                    {t("providers.test")}
                  </Button>
                  <Button size="icon" variant="ghost" onClick={() => void remove(provider)} title={t("common.delete")}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card className="mt-3">
          <PanelHeader title={t("providers.costControl")} subtitle={t("providers.costControlHint")} />
          <ul className="space-y-1 px-3.5 py-3 text-2xs leading-relaxed text-faint">
            <li>• {t("providers.cost1")}</li>
            <li>• {t("providers.cost2")}</li>
            <li>• {t("providers.cost3")}</li>
            <li>• {t("providers.cost4")}</li>
          </ul>
        </Card>
      </div>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t("providers.modalTitle")}
        description={t("providers.modalDesc")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
              {t("common.cancel")}
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void create()}>
              {t("providers.save")}
            </Button>
          </>
        }
      >
        <div className="space-y-2.5">
          <Field label={t("providers.name")}>
            <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="My OpenAI" />
          </Field>
          <Field label={t("providers.type")}>
            <Select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
              {KINDS.map((kind) => (
                <option key={kind.id} value={kind.id}>
                  {kind.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("providers.baseUrl")} hint={t("providers.baseUrlHint")}>
            <Input
              value={form.base_url}
              onChange={(event) => setForm({ ...form, base_url: event.target.value })}
              placeholder="https://api.openai.com/v1"
            />
          </Field>
          <Field label={t("providers.apiKey")}>
            <Input
              type="password"
              value={form.api_key}
              onChange={(event) => setForm({ ...form, api_key: event.target.value })}
              placeholder="sk-…"
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label={t("providers.model")}>
              <Input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} placeholder="gpt-4o-mini" />
            </Field>
            <Field label={t("providers.cheapModel")} hint={t("providers.cheapModelHint")}>
              <Input
                value={form.cheap_model}
                onChange={(event) => setForm({ ...form, cheap_model: event.target.value })}
                placeholder="gpt-4o-mini"
              />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label={t("providers.priceIn")}>
              <Input
                type="number"
                step="0.01"
                value={form.price_in_per_mtok}
                onChange={(event) => setForm({ ...form, price_in_per_mtok: Number(event.target.value) })}
              />
            </Field>
            <Field label={t("providers.priceOut")}>
              <Input
                type="number"
                step="0.01"
                value={form.price_out_per_mtok}
                onChange={(event) => setForm({ ...form, price_out_per_mtok: Number(event.target.value) })}
              />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(event) => setForm({ ...form, is_default: event.target.checked })}
              className="h-3 w-3 accent-[#5b8cff]"
            />
            {t("providers.useAsDefault")}
          </label>
        </div>
      </Modal>
    </div>
  );
}
