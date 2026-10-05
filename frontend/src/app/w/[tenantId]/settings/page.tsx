"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type S } from "@/lib/api";
import { t } from "@/lib/i18n";
import { Async, Badge, ErrorNotice, Field } from "@/components/ui";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

export default function SettingsPage() {
  const { path, can, ws, tenantId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  type Form = Record<string, string | boolean>;
  const [form, setForm] = useState<Form | null>(null);
  const s = ws.settings;
  const values: Form = form ?? { max_file_bytes: String(s.max_file_bytes), max_import_rows: String(s.max_import_rows), max_running_jobs: String(s.max_running_jobs), ai_monthly_call_budget: String(s.ai_monthly_call_budget), max_ai_context_chunks: String(s.max_ai_context_chunks), evidence_fresh_days: String(s.evidence_fresh_days), allow_self_approval: s.allow_self_approval };
  const save = useMutation({
    mutationFn: () => api("PATCH", path("/settings"), { body: { max_file_bytes: Number(values.max_file_bytes), max_import_rows: Number(values.max_import_rows), max_running_jobs: Number(values.max_running_jobs), ai_monthly_call_budget: Number(values.ai_monthly_call_budget), max_ai_context_chunks: Number(values.max_ai_context_chunks), evidence_fresh_days: Number(values.evidence_fresh_days), allow_self_approval: values.allow_self_approval } }),
    onSuccess: () => { toast("Einstellungen gespeichert.", "ok"); void qc.invalidateQueries({ queryKey: ["workspace", tenantId] }); setForm(null); },
  });
  const policies = useQuery({ queryKey: ["policies"], queryFn: () => api<S["PolicyOut"][]>("GET", path("/scoring-policies")) });
  const activate = useMutation({ mutationFn: (id: string) => api("POST", path(`/scoring-policies/${id}/activate`)), onSuccess: () => void qc.invalidateQueries({ queryKey: ["policies"] }) });
  if (!can("admin")) return <><h1>Einstellungen</h1><div className="notice warn">Einstellungen sind für Admins und Owner verfügbar.</div></>;
  const f = (k: string) => String(values[k]);
  const set = (k: string, v: string | boolean) => setForm({ ...values, [k]: v });
  return (
    <>
      <div className="page-head"><div><h1>Einstellungen</h1><p className="muted">Grenzen und Regeln dieses Workspaces ({ws.name}, Zeitzone {ws.timezone}).</p></div></div>
      <div className="grid cols-2">
        <form className="card" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2>Grenzen</h2>
          <Field id="s-fb" label="Maximale Dateigröße (Bytes)"><input id="s-fb" type="number" min={1024} max={104857600} value={f("max_file_bytes")} onChange={(e) => set("max_file_bytes", e.target.value)} /></Field>
          <Field id="s-rows" label="Maximale Importzeilen"><input id="s-rows" type="number" min={1} max={100000} value={f("max_import_rows")} onChange={(e) => set("max_import_rows", e.target.value)} /></Field>
          <Field id="s-jobs" label="Gleichzeitig laufende Aufgaben"><input id="s-jobs" type="number" min={1} max={50} value={f("max_running_jobs")} onChange={(e) => set("max_running_jobs", e.target.value)} /></Field>
          <Field id="s-ai" label={`KI-Aufrufbudget pro Monat (bisher ${s.ai_calls_this_month})`}><input id="s-ai" type="number" min={0} value={f("ai_monthly_call_budget")} onChange={(e) => set("ai_monthly_call_budget", e.target.value)} /></Field>
          <Field id="s-ctx" label="Maximale Belege je Analyse"><input id="s-ctx" type="number" min={1} max={5000} value={f("max_ai_context_chunks")} onChange={(e) => set("max_ai_context_chunks", e.target.value)} /></Field>
          <Field id="s-fresh" label="Beleg gilt als aktuell bis (Tage)"><input id="s-fresh" type="number" min={1} max={3650} value={f("evidence_fresh_days")} onChange={(e) => set("evidence_fresh_days", e.target.value)} /></Field>
          <label className="check"><input type="checkbox" checked={Boolean(values.allow_self_approval)} onChange={(e) => set("allow_self_approval", e.target.checked)} /> Selbstfreigabe erlauben (hebt das Vier-Augen-Prinzip auf)</label>
          {save.isError ? <ErrorNotice error={save.error} /> : null}
          <div style={{ marginTop: ".75rem" }}><button className="btn primary" type="submit" disabled={save.isPending}>{t.common.save}</button></div>
        </form>
        <section className="card"><h2>Scoring-Policies</h2>
          <Async q={policies}>{(d) => <ul>{d.map((p) => <li key={p.id}><strong>{p.name}</strong> {p.active ? <Badge kind="ok">aktiv</Badge> : <button className="btn small" onClick={() => activate.mutate(p.id)}>Aktivieren</button>}<div className="small muted">{Object.entries(p.weights).map(([k, v]) => `${t.criteria[k]}: ${String(v)}`).join(", ")}; fehlende Werte: {p.missing_value_policy}; Währung {String((p.parameters as Record<string, unknown>).currency)}</div></li>)}</ul>}</Async>
          <p className="small muted">Neue Policies legen Sie über die API an (POST /scoring-policies). Gewichte müssen sich zu 1 summieren und dürfen nur die definierten Kriterien enthalten.</p>
          <h3>KI-Anbieter</h3><p>{ws.ai.demo ? "Demo-Adapter (deterministisch, ohne externe Aufrufe)." : ws.ai.provider === "ollama" ? `Ollama (lokales Modell ${ws.ai.model}), Daten verlassen den eigenen Server nicht.` : `Anthropic, Modell ${ws.ai.model}.`}</p>
        </section>
      </div>
    </>
  );
}
