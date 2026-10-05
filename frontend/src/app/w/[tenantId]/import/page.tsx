"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtBytes, fmtDate } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Badge, ErrorNotice, Field, Pager, StatusBadge, Tabs } from "@/components/ui";
import { JobProgress } from "@/components/jobs";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type ImportOut = S["ImportOut"];
interface Preview {
  kind: string; blocking: boolean; blocking_reasons: string[]; counts: Record<string, number>; error_total: number; duplicate_total: number;
  errors: { row: number; field: string; code: string; message: string }[]; warning_counts: Record<string, number>;
  warnings: { row: number; field: string; code: string; message: string }[];
  duplicates: { row: number; reason: string; key: string }[];
  sample: { row: number; status: string; values?: Record<string, string | null> }[];
  document?: { pages: number | null; characters: number; chunks: number; no_text_layer: boolean; chunk_samples: { locator: Record<string, unknown>; text: string }[] };
}
interface Detected { header?: string[]; row_count?: number; delimiter?: string; suggested_mapping?: Record<string, string | null> }

const KINDS = [
  { id: "customers", label: "Kundenstammdaten (CSV)", accept: ".csv,text/csv", fields: ["external_id", "name", "segment", "country", "commercial_value", "value_basis", "currency", "value_as_of"], required: ["external_id", "name"] },
  { id: "opportunities", label: "Verkaufschancen (CSV)", accept: ".csv,text/csv", fields: ["external_id", "customer_external_id", "name", "stage", "amount", "currency", "closed_at"], required: ["external_id", "customer_external_id", "name", "stage"] },
  { id: "feedback", label: "Kundenfeedback (CSV)", accept: ".csv,text/csv", fields: ["external_id", "customer_external_id", "opportunity_external_id", "channel", "occurred_at", "body", "language"], required: ["occurred_at", "body"] },
  { id: "document", label: "Dokument (PDF, TXT, Markdown)", accept: ".pdf,.txt,.md,application/pdf,text/plain", fields: [], required: [] },
] as const;
const FIELD_LABEL: Record<string, string> = {
  external_id: "Externe ID", name: "Name", segment: "Segment", country: "Land", commercial_value: "Umsatzwert", value_basis: "Wertbasis (arr/annual_sales)", currency: "Währung",
  value_as_of: "Wert-Stand (Datum)", customer_external_id: "Kunden-ID", stage: "Phase", amount: "Betrag", closed_at: "Abschlussdatum", opportunity_external_id: "Opportunity-ID",
  channel: "Kanal", occurred_at: "Datum", body: "Feedbacktext", language: "Sprache",
};

export default function ImportPage() {
  const { can } = useWorkspace();
  const [tab, setTab] = useState("file");
  const [batch, setBatch] = useState<ImportOut | null>(null);
  if (!can("editor")) return <><h1>Import</h1><div className="notice warn">Importe sind für Editoren, Admins und Owner verfügbar. Ihre Rolle darf Importe nur einsehen.</div><History onOpen={() => undefined} readOnly /></>;
  return (
    <>
      <div className="page-head"><div><h1>Import</h1><p className="muted">CSV-Dateien, Dokumente und Gesprächsnotizen werden geprüft, bevor etwas gespeichert wird. Importiert wird nur als Ganzes.</p></div></div>
      <Tabs tabs={[{ id: "file", label: "Datei importieren" }, { id: "text", label: "Gesprächsnotiz einfügen" }, { id: "history", label: "Verlauf" }]} value={tab} onChange={setTab} />
      {tab === "file" ? <FileImport batch={batch} setBatch={setBatch} /> : null}
      {tab === "text" ? <TextImport onCreated={(b) => { setBatch(b); setTab("file"); }} /> : null}
      {tab === "history" ? <History onOpen={(b) => { setBatch(b); setTab("file"); }} /> : null}
    </>
  );
}

function FileImport({ batch, setBatch }: { batch: ImportOut | null; setBatch: (b: ImportOut | null) => void }) {
  const { path } = useWorkspace();
  const toast = useToast();
  const [kind, setKind] = useState<string>("customers");
  const [file, setFile] = useState<File | null>(null);
  const [synthetic, setSynthetic] = useState(false);
  const upload = useMutation({
    mutationFn: () => { const f = new FormData(); f.set("kind", kind); f.set("synthetic", String(synthetic)); f.set("file", file!); return api<ImportOut>("POST", path("/imports"), { form: f }); },
    onSuccess: (b) => { setBatch(b); toast("Datei hochgeladen. Bitte Zuordnung prüfen.", "ok"); },
  });
  if (batch) return <Wizard batch={batch} onChange={setBatch} onReset={() => { setBatch(null); setFile(null); upload.reset(); }} />;
  const k = KINDS.find((x) => x.id === kind)!;
  return (
    <form className="card" style={{ maxWidth: 640 }} onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate(); }}>
      <h2>1. Datei wählen</h2>
      <Field id="kind" label="Art der Daten" hint="Reihenfolge: erst Kunden, dann Opportunities, dann Feedback (Verweise werden geprüft).">
        <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)} data-testid="import-kind">{KINDS.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}</select>
      </Field>
      <Field id="file" label="Datei" hint="CSV (UTF-8 oder Windows-1252, Trennzeichen automatisch), TXT, Markdown oder PDF mit Textebene.">
        <input id="file" type="file" accept={k.accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} data-testid="import-file" />
      </Field>
      <label className="check"><input type="checkbox" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} /> Die Daten sind synthetisch (Demo/Test)</label>
      {upload.isError ? <ErrorNotice error={upload.error} /> : null}
      <div className="row" style={{ marginTop: ".75rem" }}>
        <button className="btn primary" type="submit" disabled={!file || upload.isPending} data-testid="import-upload">{upload.isPending ? "Lädt hoch …" : "Hochladen und prüfen"}</button>
      </div>
    </form>
  );
}

function TextImport({ onCreated }: { onCreated: (b: ImportOut) => void }) {
  const { path } = useWorkspace();
  const [f, setF] = useState({ title: "", text: "", customer_external_id: "", occurred_at: "", channel: "call" });
  const m = useMutation({
    mutationFn: () => api<ImportOut>("POST", path("/imports/text"), { body: { ...f, customer_external_id: f.customer_external_id || null, occurred_at: f.occurred_at || null } }),
    onSuccess: onCreated,
  });
  return (
    <form className="card" style={{ maxWidth: 720 }} onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <h2>Gesprächsnotiz als Text</h2>
      <Field id="n-title" label="Titel"><input id="n-title" required maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <Field id="n-text" label="Text" hint="Absätze werden als einzelne Fundstellen erfasst (leere Zeile = neuer Absatz)."><textarea id="n-text" required minLength={10} rows={8} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} /></Field>
      <div className="grid cols-3">
        <Field id="n-cust" label="Kunden-ID (optional)"><input id="n-cust" value={f.customer_external_id} onChange={(e) => setF({ ...f, customer_external_id: e.target.value })} placeholder="z. B. K-1013" /></Field>
        <Field id="n-date" label="Datum (optional)"><input id="n-date" type="date" value={f.occurred_at} onChange={(e) => setF({ ...f, occurred_at: e.target.value })} /></Field>
        <Field id="n-ch" label="Kanal"><select id="n-ch" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{["call", "interview", "email", "chat", "survey", "other"].map((c) => <option key={c} value={c}>{t.channel[c]}</option>)}</select></Field>
      </div>
      {m.isError ? <ErrorNotice error={m.error} /> : null}
      <button className="btn primary" type="submit" disabled={m.isPending}>Prüfen</button>
    </form>
  );
}

function Wizard({ batch, onChange, onReset }: { batch: ImportOut; onChange: (b: ImportOut) => void; onReset: () => void }) {
  const { path } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const kind = KINDS.find((k) => k.id === batch.kind);
  const detected = batch.detected as Detected;
  const preview = batch.preview as Preview | null;
  const [mapping, setMapping] = useState<Record<string, string | null>>(batch.mapping as Record<string, string | null>);
  const [options, setOptions] = useState<Record<string, unknown>>(batch.options as Record<string, unknown>);
  const [jobId, setJobId] = useState<string | null>(batch.job_id ?? null);
  const run = useMutation({
    mutationFn: () => api<ImportOut>("PUT", path(`/imports/${batch.id}/settings`), { body: { mapping: kind && kind.fields.length ? mapping : undefined, options } }),
    onSuccess: onChange,
  });
  const commit = useMutation({
    mutationFn: () => api<{ job_id: string }>("POST", path(`/imports/${batch.id}/commit`)),
    onSuccess: (r) => { setJobId(r.job_id); void qc.invalidateQueries({ queryKey: ["imports"] }); toast("Import wurde gestartet.", "ok"); },
  });
  const isCsv = !!kind && kind.fields.length > 0;
  const finished = batch.status === "committed";
  return (
    <div className="stack">
      <div className="card">
        <div className="row"><h2 style={{ margin: 0 }}>{kind?.label ?? batch.kind}</h2><StatusBadge value={batch.status} map={t.status.import} /><span className="small muted">{batch.file_name} · {fmtBytes(batch.file_size)}</span><span className="spacer" /><button className="btn small" onClick={onReset}>Neuer Import</button></div>
      </div>
      {isCsv && !jobId && !finished ? (
        <section className="card" aria-labelledby="map">
          <h2 id="map">2. Spalten zuordnen</h2>
          <p className="small muted">{detected.row_count} Datenzeilen, Trennzeichen „{detected.delimiter}“. Pflichtfelder sind markiert.</p>
          <div className="grid cols-3">
            {kind!.fields.map((f) => (
              <Field key={f} id={`map-${f}`} label={`${FIELD_LABEL[f] ?? f}${(kind!.required as readonly string[]).includes(f) ? " *" : ""}`}>
                <select id={`map-${f}`} value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value || null })} data-testid={`map-${f}`}>
                  <option value="">(nicht zuordnen)</option>
                  {(detected.header ?? []).map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </Field>
            ))}
          </div>
          {kind!.id !== "feedback" ? (
            <Field id="dup" label="Bereits vorhandene Datensätze (gleiche externe ID)"><select id="dup" value={String(options.on_duplicate ?? "skip")} onChange={(e) => setOptions({ ...options, on_duplicate: e.target.value })}><option value="skip">überspringen</option><option value="update">aktualisieren</option></select></Field>
          ) : null}
          <button className="btn primary" onClick={() => run.mutate()} disabled={run.isPending} data-testid="import-preview">{run.isPending ? "Prüft …" : "Vorschau berechnen"}</button>
        </section>
      ) : null}
      {!isCsv && !jobId && !finished ? (
        <section className="card">
          <h2>2. Angaben zum Dokument</h2>
          <div className="grid cols-3">
            <Field id="d-title" label="Titel"><input id="d-title" value={String(options.title ?? "")} onChange={(e) => setOptions({ ...options, title: e.target.value })} /></Field>
            <Field id="d-cust" label="Kunden-ID (optional)"><input id="d-cust" value={String(options.customer_external_id ?? "")} onChange={(e) => setOptions({ ...options, customer_external_id: e.target.value })} /></Field>
            <Field id="d-date" label="Datum (optional)"><input id="d-date" type="date" value={String(options.occurred_at ?? "")} onChange={(e) => setOptions({ ...options, occurred_at: e.target.value })} /></Field>
          </div>
          <button className="btn primary" onClick={() => run.mutate()} disabled={run.isPending} data-testid="import-preview">Vorschau berechnen</button>
        </section>
      ) : null}
      {run.isError ? <ErrorNotice error={run.error} /> : null}
      {preview ? <PreviewCard preview={preview} /> : null}
      {preview && !jobId && !finished ? (
        <div className="card">
          <h2>3. Bestätigen</h2>
          {preview.blocking ? <div className="notice danger" role="alert"><strong>Dieser Import kann so nicht bestätigt werden.</strong><ul>{preview.blocking_reasons.map((r) => <li key={r}>{r}</li>)}</ul></div>
            : <p>Nach der Bestätigung werden die Daten mandantensicher gespeichert und als Quellen mit Fundstellen erfasst.</p>}
          {commit.isError ? <ErrorNotice error={commit.error} /> : null}
          <button className="btn primary" disabled={preview.blocking || commit.isPending} onClick={() => commit.mutate()} data-testid="import-commit">Import bestätigen</button>
        </div>
      ) : null}
      {jobId ? <JobProgress jobId={jobId} label="Import" onDone={() => void qc.invalidateQueries({ queryKey: ["import", batch.id] })} /> : null}
      {finished && batch.result ? <div className="notice ok" data-testid="import-result">Importiert: {String((batch.result as Record<string, unknown>).created)} neu, {String((batch.result as Record<string, unknown>).updated)} aktualisiert, {String((batch.result as Record<string, unknown>).skipped_duplicates)} Dubletten übersprungen.</div> : null}
      {jobId ? <ImportDone batchId={batch.id} onChange={onChange} /> : null}
    </div>
  );
}

function ImportDone({ batchId, onChange }: { batchId: string; onChange: (b: ImportOut) => void }) {
  const { path } = useWorkspace();
  useQuery({ queryKey: ["import", batchId], queryFn: async () => { const b = await api<ImportOut>("GET", path(`/imports/${batchId}`)); onChange(b); return b; }, refetchInterval: (q) => (q.state.data?.status === "committing" ? 2000 : false) });
  return null;
}

function PreviewCard({ preview }: { preview: Preview }) {
  const c = preview.counts;
  return (
    <section className="card" aria-labelledby="prev" data-testid="import-preview-card">
      <h2 id="prev">Vorschau</h2>
      <div className="row" style={{ marginBottom: ".5rem" }}>
        <Badge>{c.total_rows} Zeilen</Badge>
        <Badge kind="ok">{c.new ?? 0} neu</Badge>
        {c.update ? <Badge kind="info">{c.update} werden aktualisiert</Badge> : null}
        <Badge kind={preview.duplicate_total ? "warn" : undefined}>{preview.duplicate_total} Dubletten</Badge>
        <Badge kind={preview.error_total ? "danger" : undefined}>{preview.error_total} Fehler</Badge>
        {c.warning_rows ? <Badge kind="warn">{c.warning_rows} Zeilen mit Hinweisen</Badge> : null}
      </div>
      {preview.document ? (
        <div className="notice" role="note">{preview.document.pages ? `${preview.document.pages} Seiten, ` : ""}{preview.document.characters} Zeichen, {preview.document.chunks} Fundstellen.{preview.document.no_text_layer ? " Keine Textebene gefunden (Scan)." : ""}
          {preview.document.chunk_samples.map((s, i) => <div className="quote" key={i}><span className="small muted">{Object.entries(s.locator).filter(([k]) => k !== "file").map(([k, v]) => `${k} ${v}`).join(", ")}</span><div>{s.text}</div></div>)}</div>
      ) : null}
      {preview.errors.length ? (
        <><h3>Fehler (Zeilennummer ohne Kopfzeile)</h3>
          <div className="table-wrap"><table><thead><tr><th>Zeile</th><th>Feld</th><th>Problem</th></tr></thead><tbody>{preview.errors.slice(0, 50).map((e, i) => <tr key={i}><td>{e.row}</td><td>{e.field}</td><td>{e.message}</td></tr>)}</tbody></table></div></>
      ) : null}
      {preview.duplicates.length ? (
        <><h3>Dubletten</h3><ul className="small">{preview.duplicates.slice(0, 10).map((d, i) => <li key={i}>Zeile {d.row}: {d.reason === "duplicate_in_file" ? "Dublette innerhalb der Datei" : "bereits importiert"} ({d.key})</li>)}</ul></>
      ) : null}
      {Object.keys(preview.warning_counts).length ? <div className="notice warn small">Hinweise: {Object.entries(preview.warning_counts).map(([k, v]) => `${v}× ${k}`).join(", ")}. Fehlende Werte bleiben unbekannt und werden nicht als 0 gerechnet.</div> : null}
      {preview.sample.some((s) => s.values) ? (
        <><h3>Beispielzeilen</h3><div className="table-wrap"><table><thead><tr><th>Zeile</th><th>Status</th><th>Werte</th></tr></thead><tbody>{preview.sample.map((s) => (
          <tr key={s.row}><td>{s.row}</td><td>{s.status}</td><td className="small">{s.values ? Object.entries(s.values).filter(([, v]) => v).map(([k, v]) => `${k}: ${String(v).slice(0, 60)}`).join(" · ") : ""}</td></tr>))}</tbody></table></div></>
      ) : null}
    </section>
  );
}

function History({ onOpen, readOnly }: { onOpen: (b: ImportOut) => void; readOnly?: boolean }) {
  const { path, ws } = useWorkspace();
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ["imports", offset], queryFn: () => api<Page<ImportOut>>("GET", path("/imports"), { query: { limit: 15, offset } }) });
  return (
    <Async q={q} isEmpty={(d) => d.items.length === 0} empty="Noch keine Importe.">
      {(d) => (
        <div className="card"><div className="table-wrap"><table><thead><tr><th>Datei</th><th>Art</th><th>Status</th><th>Hochgeladen</th><th /></tr></thead><tbody>
          {d.items.map((b) => (
            <tr key={b.id}><td>{b.file_name}</td><td>{b.kind}</td><td><StatusBadge value={b.status} map={t.status.import} /></td><td>{fmtDate(b.created_at, ws.timezone, true)}</td>
              <td>{!readOnly && b.status !== "committed" ? <button className="btn small" onClick={() => onOpen(b)}>Öffnen</button> : null}</td></tr>
          ))}</tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>
      )}
    </Async>
  );
}
