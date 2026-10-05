"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate, locatorText } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Badge, DemoBadge, ErrorNotice, Field, Modal, StatusBadge, Tabs } from "@/components/ui";
import { MetricsPanel, type Metrics } from "@/components/metrics";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type Detail = S["ProblemDetail"];
type Evidence = S["EvidenceOut"];

export default function ProblemPage() {
  const { id } = useParams<{ id: string }>();
  const { path, can, ws, tenantId } = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const q = useQuery({ queryKey: ["problem", id], queryFn: () => api<Detail>("GET", path(`/problems/${id}`)) });
  const [tab, setTab] = useState("evidence");
  const [sel, setSel] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [modal, setModal] = useState<null | "edit" | "split" | "add" | "initiative">(null);
  const set = (d: Detail) => { qc.setQueryData(["problem", id], d); void qc.invalidateQueries({ queryKey: ["problems"] }); };
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Detail>("PATCH", path(`/problems/${id}`), { body, version: q.data!.version }),
    onSuccess: (d) => { set(d); toast("Gespeichert.", "ok"); setModal(null); },
    onError: (e) => toast(e instanceof Error ? e.message : "Fehler", "error"),
  });
  const del = useMutation({ mutationFn: () => api("DELETE", path(`/problems/${id}`)), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["problems"] }); router.push(`/w/${tenantId}/problems`); }, onError: (e) => toast(e instanceof Error ? e.message : "Fehler", "error") });
  const editor = can("editor");
  return (
    <Async q={q}>
      {(p) => {
        const m = p.metrics as unknown as Metrics;
        const selected = p.evidence.find((e) => e.id === sel) ?? null;
        const groups: [string, Evidence[]][] = (["supports", "contradicts", "context"] as const).map((r) => [r, p.evidence.filter((e) => e.relation === r)]);
        return (
          <>
            <div className="page-head">
              <div>
                <p className="small"><Link href={`/w/${tenantId}/problems`}>← Probleme</Link></p>
                <h1 data-testid="problem-title">{p.title}</h1>
                <div className="row"><StatusBadge value={p.status} map={t.status.problem} />{p.ai?.demo ? <DemoBadge /> : null}{p.origin === "ai" ? <Badge>Vorschlag der Analyse</Badge> : null}{p.owner_name ? <span className="small muted">Verantwortlich: {p.owner_name}</span> : null}
                  {p.merged_into_problem_id ? <Link className="small" href={`/w/${tenantId}/problems/${p.merged_into_problem_id}`}>zusammengeführt in anderes Problem →</Link> : null}</div>
                {p.description ? <p className="muted">{p.description}</p> : null}
              </div>
              {editor ? <div className="actions">
                {p.status !== "confirmed" && p.status !== "archived" ? <button className="btn primary" onClick={() => patch.mutate({ status: "confirmed" })} data-testid="problem-confirm">Bestätigen</button> : null}
                {p.status !== "archived" ? <button className="btn" onClick={() => patch.mutate({ status: "archived" })}>Archivieren</button> : <button className="btn" disabled={!!p.merged_into_problem_id} onClick={() => patch.mutate({ status: "proposed" })}>Wiederherstellen</button>}
                <button className="btn" onClick={() => setModal("edit")}>Bearbeiten</button>
                <button className="btn" onClick={() => setModal("initiative")} disabled={p.status === "archived"} data-testid="new-initiative">Initiative anlegen</button>
              </div> : null}
            </div>
            {p.status === "proposed" ? <div className="notice warn" role="note">Dieses Problem ist ein ungeprüfter Vorschlag. Prüfen Sie die Belege, korrigieren Sie die Zuordnung und bestätigen Sie es dann.</div> : null}
            <Tabs tabs={[{ id: "evidence", label: `Belege (${p.evidence.length})` }, { id: "metrics", label: "Kennzahlen & Kontext" }, { id: "history", label: "Verlauf" }, { id: "initiatives", label: `Initiativen (${p.initiatives.length})` }]} value={tab} onChange={setTab} />
            <div className="split">
              <div>
                {tab === "metrics" ? <MetricsPanel m={m} /> : null}
                {tab === "evidence" ? (
                  <div className="stack">
                    {editor ? <div className="row"><button className="btn" onClick={() => setModal("add")}>Beleg hinzufügen</button><button className="btn" disabled={checked.size === 0 || checked.size >= p.evidence.length} onClick={() => setModal("split")} data-testid="split-open">Ausgewählte Belege abspalten ({checked.size})</button></div> : null}
                    {groups.map(([rel, items]) => (
                      <section className="card" key={rel} aria-label={t.relation[rel]}>
                        <h2>{rel === "supports" ? "Unterstützende Belege" : rel === "contradicts" ? "Widersprechende Signale" : "Kontext"} ({items.length})</h2>
                        {items.length === 0 ? <p className="muted">Keine.</p> : items.map((e) => (
                          <div key={e.id} className={`quote ${e.relation}`} aria-current={sel === e.id}>
                            <div className="row small muted">
                              {editor ? <input type="checkbox" aria-label="Beleg auswählen" checked={checked.has(e.id)} onChange={() => setChecked((s) => { const n = new Set(s); if (n.has(e.id)) n.delete(e.id); else n.add(e.id); return n; })} /> : null}
                              <span>{e.customer_name ?? "ohne Kunde"}{e.customer_segment ? ` (${e.customer_segment})` : ""}</span><span>· {fmtDate(e.occurred_at, ws.timezone)}</span><span>· {t.channel[e.channel] ?? e.channel}</span>
                              {e.human_verified ? <Badge kind="ok">verifiziert</Badge> : <Badge>ungeprüft</Badge>}
                              {e.origin === "ai" ? <Badge>Analyse</Badge> : null}
                            </div>
                            <div>„{e.quote}“</div>
                            <button className="btn link small" onClick={() => setSel(e.id)} data-testid="open-evidence">Beleg im Seitenpanel öffnen</button>
                          </div>))}
                      </section>))}
                  </div>
                ) : null}
                {tab === "history" ? (
                  <div className="card"><h2>Verlauf (Zusammenführen, Aufteilen, Korrekturen)</h2>
                    {p.history.length === 0 ? <p className="muted">Keine Einträge.</p> : <ul>{p.history.map((h) => <li key={h.id}><span className="small muted">{fmtDate(h.created_at, ws.timezone, true)}</span> · {h.actor_name ?? "System"}: <strong>{ACTION[h.action] ?? h.action}</strong> <span className="small muted">{summ(h.details)}</span></li>)}</ul>}
                  </div>
                ) : null}
                {tab === "initiatives" ? (
                  <div className="card"><h2>Initiativen</h2>{p.initiatives.length === 0 ? <p className="muted">Noch keine Initiative.</p> : <ul>{p.initiatives.map((i) => <li key={String(i.id)}><Link href={`/w/${tenantId}/initiatives/${i.id}`}>{String(i.title)}</Link> <StatusBadge value={String(i.status)} map={t.status.initiative} /></li>)}</ul>}</div>
                ) : null}
              </div>
              <aside className="panel" aria-label="Beleg-Seitenpanel" data-testid="evidence-panel">
                <EvidencePanel e={selected} problem={p} onChanged={set} onClose={() => setSel(null)} />
              </aside>
            </div>
            {editor && p.status !== "confirmed" ? <div style={{ marginTop: "1.5rem" }}><button className="btn danger small" onClick={() => { if (window.confirm("Problem endgültig löschen?")) del.mutate(); }}>Problem löschen</button></div> : null}
            <EditModal open={modal === "edit"} onClose={() => setModal(null)} p={p} onSave={(b) => patch.mutate(b)} busy={patch.isPending} />
            <SplitModal open={modal === "split"} onClose={() => setModal(null)} p={p} ids={[...checked]} onDone={(n) => { setChecked(new Set()); setModal(null); void qc.invalidateQueries({ queryKey: ["problem", id] }); void qc.invalidateQueries({ queryKey: ["problems"] }); router.push(`/w/${tenantId}/problems/${n.id}`); }} />
            <AddEvidenceModal open={modal === "add"} onClose={() => setModal(null)} p={p} onDone={(d) => { set(d); setModal(null); }} />
            <InitiativeModal open={modal === "initiative"} onClose={() => setModal(null)} p={p} />
          </>
        );
      }}
    </Async>
  );
}

const ACTION: Record<string, string> = {
  created: "angelegt", edited: "bearbeitet", status_changed: "Status geändert", merged_into: "in anderes Problem zusammengeführt", merged_from: "Probleme zusammengeführt", split_to: "Belege abgespalten",
  split_from: "durch Abspaltung entstanden", evidence_added: "Beleg hinzugefügt", evidence_removed: "Beleg entfernt", evidence_moved_out: "Beleg verschoben (heraus)", evidence_moved_in: "Beleg verschoben (hinein)",
  evidence_relation_changed: "Beziehung korrigiert", evidence_verified: "Beleg verifiziert", evidence_unverified: "Verifizierung zurückgenommen",
};
function summ(d: Record<string, unknown>): string {
  const bits: string[] = [];
  if (d.old && d.new) bits.push(`${String(d.old)} → ${String(d.new)}`);
  if (d.target_title) bits.push(`Ziel: ${String(d.target_title)}`);
  if (d.source_title) bits.push(`Quelle: ${String(d.source_title)}`);
  if (typeof d.evidence_moved === "number") bits.push(`${d.evidence_moved} Belege`);
  if (typeof d.duplicates_dropped === "number" && d.duplicates_dropped) bits.push(`${d.duplicates_dropped} Dubletten entfernt`);
  return bits.join(" · ");
}

function EvidencePanel({ e, problem, onChanged, onClose }: { e: Evidence | null; problem: Detail; onChanged: (d: Detail) => void; onClose: () => void }) {
  const { path, can, ws, tenantId } = useWorkspace();
  const toast = useToast();
  const others = useQuery({ queryKey: ["problems-all"], enabled: !!e, queryFn: () => api<Page<S["ProblemListItem"]>>("GET", path("/problems"), { query: { limit: 100, sort: "title" } }) });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Detail>("PATCH", path(`/evidence/${e!.id}`), { body }),
    onSuccess: (d, vars) => { if (vars.move_to_problem_id) onClose(); onChanged(d); toast("Beleg aktualisiert.", "ok"); },
    onError: (err) => toast(err instanceof Error ? err.message : "Fehler", "error"),
  });
  const remove = useMutation({ mutationFn: () => api<Detail>("DELETE", path(`/evidence/${e!.id}`)), onSuccess: (d) => { onChanged(d); onClose(); toast("Beleg entfernt.", "ok"); } });
  if (!e) return <div className="card muted">Wählen Sie einen Beleg, um Originaltext, Fundstelle und Korrekturmöglichkeiten zu sehen.</div>;
  const idx = e.chunk_text.toLowerCase().indexOf(e.quote.toLowerCase());
  return (
    <div className="card">
      <div className="row"><h2 style={{ margin: 0 }}>Originalbeleg</h2><span className="spacer" /><button className="btn small" onClick={onClose}>{t.common.close}</button></div>
      <dl className="kv" style={{ margin: ".5rem 0" }}>
        <dt>Kunde</dt><dd>{e.customer_id ? <Link href={`/w/${tenantId}/customers?id=${e.customer_id}`}>{e.customer_name}</Link> : "ohne Kunde"}{e.customer_segment ? ` (${e.customer_segment})` : ""}</dd>
        <dt>Datum</dt><dd>{fmtDate(e.occurred_at, ws.timezone, true)}</dd><dt>Kanal</dt><dd>{t.channel[e.channel] ?? e.channel}</dd>
        <dt>Quelle</dt><dd>{e.source_title} <Badge kind={e.source_origin === "synthetic" ? "warn" : undefined}>{t.origin[e.source_origin] ?? e.source_origin}</Badge></dd>
        <dt>Fundstelle</dt><dd data-testid="evidence-locator">{locatorText(e.locator) || "–"}</dd>
        {e.opportunity_name ? <><dt>Opportunity</dt><dd>{e.opportunity_name} ({t.stage[e.opportunity_stage ?? ""] ?? e.opportunity_stage})</dd></> : null}
      </dl>
      <div className={`quote ${e.relation}`} data-testid="evidence-text">
        {idx >= 0 ? <>{e.chunk_text.slice(0, idx)}<mark>{e.chunk_text.slice(idx, idx + e.quote.length)}</mark>{e.chunk_text.slice(idx + e.quote.length)}</> : e.chunk_text}
      </div>
      <p className="small muted">Das markierte Zitat steht wörtlich im Originaltext (serverseitig geprüft).</p>
      <Link className="small" href={`/w/${tenantId}/sources`}>Alle Quellen</Link>
      {can("editor") ? (
        <div className="stack" style={{ marginTop: ".75rem" }}>
          <Field id="ev-rel" label="Beziehung zum Problem"><select id="ev-rel" value={e.relation} onChange={(ev) => patch.mutate({ relation: ev.target.value })} data-testid="evidence-relation">{Object.entries(t.relation).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <label className="check"><input type="checkbox" checked={e.human_verified} onChange={(ev) => patch.mutate({ human_verified: ev.target.checked })} data-testid="evidence-verified" /> Von einem Menschen geprüft</label>
          <Field id="ev-move" label="In anderes Problem verschieben"><select id="ev-move" value="" onChange={(ev) => ev.target.value && patch.mutate({ move_to_problem_id: ev.target.value })}><option value="">Problem wählen …</option>{others.data?.items.filter((o) => o.id !== problem.id).map((o) => <option key={o.id} value={o.id}>{o.title}</option>)}</select></Field>
          <button className="btn danger small" onClick={() => { if (window.confirm("Beleg aus diesem Problem entfernen? Die Quelle bleibt erhalten.")) remove.mutate(); }}>Beleg aus Problem entfernen</button>
        </div>
      ) : null}
    </div>
  );
}

function EditModal({ open, onClose, p, onSave, busy }: { open: boolean; onClose: () => void; p: Detail; onSave: (b: Record<string, unknown>) => void; busy: boolean }) {
  const { path } = useWorkspace();
  const members = useQuery({ queryKey: ["members"], enabled: open, queryFn: () => api<S["MemberOut"][]>("GET", path("/members")) });
  const [title, setTitle] = useState(p.title);
  const [desc, setDesc] = useState(p.description);
  const [owner, setOwner] = useState(p.owner_user_id ?? "");
  return (
    <Modal open={open} onClose={onClose} title="Problem bearbeiten" footer={<button className="btn primary" disabled={busy || !title.trim()} onClick={() => onSave({ title, description: desc, ...(owner ? { owner_user_id: owner } : { clear_owner: true }) })} data-testid="edit-save">Speichern</button>}>
      <Field id="e-title" label="Titel"><input id="e-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} data-testid="edit-title" /></Field>
      <Field id="e-desc" label="Beschreibung"><textarea id="e-desc" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
      <Field id="e-owner" label="Verantwortlich"><select id="e-owner" value={owner} onChange={(e) => setOwner(e.target.value)}><option value="">niemand</option>{members.data?.filter((m) => m.status === "active").map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name}</option>)}</select></Field>
    </Modal>
  );
}

function SplitModal({ open, onClose, p, ids, onDone }: { open: boolean; onClose: () => void; p: Detail; ids: string[]; onDone: (d: Detail) => void }) {
  const { path } = useWorkspace();
  const [title, setTitle] = useState("");
  const m = useMutation({ mutationFn: () => api<Detail>("POST", path(`/problems/${p.id}/split`), { body: { evidence_ids: ids, title } }), onSuccess: onDone });
  return (
    <Modal open={open} onClose={onClose} title="Belege abspalten" footer={<button className="btn primary" disabled={!title.trim() || m.isPending} onClick={() => m.mutate()} data-testid="split-confirm">Neues Problem anlegen</button>}>
      <p>{ids.length} ausgewählte Belege wandern in ein neues Problem. Beide Probleme erhalten einen Verlaufseintrag.</p>
      <Field id="s-title" label="Titel des neuen Problems"><input id="s-title" value={title} onChange={(e) => setTitle(e.target.value)} data-testid="split-title" /></Field>
      {m.isError ? <ErrorNotice error={m.error} /> : null}
    </Modal>
  );
}

function AddEvidenceModal({ open, onClose, p, onDone }: { open: boolean; onClose: () => void; p: Detail; onDone: (d: Detail) => void }) {
  const { path, ws } = useWorkspace();
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [relation, setRelation] = useState("supports");
  const res = useQuery({ queryKey: ["feedback-pick", applied], enabled: open && applied.length > 0, queryFn: () => api<Page<S["FeedbackOut"]>>("GET", path("/feedback"), { query: { q: applied, limit: 8 } }) });
  const add = useMutation({ mutationFn: (chunk: string) => api<Detail>("POST", path(`/problems/${p.id}/evidence`), { body: { source_chunk_id: chunk, relation } }), onSuccess: onDone });
  return (
    <Modal open={open} onClose={onClose} title="Beleg hinzufügen">
      <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(q); }}><input aria-label="Feedback suchen" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Feedback durchsuchen …" style={{ flex: 1 }} /><button className="btn" type="submit">{t.common.search}</button></form>
      <Field id="a-rel" label="Beziehung"><select id="a-rel" value={relation} onChange={(e) => setRelation(e.target.value)}>{Object.entries(t.relation).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
      {res.data?.items.map((f) => (
        <div key={f.id} className="quote"><div className="small muted">{f.customer_name ?? "ohne Kunde"} · {fmtDate(f.occurred_at, ws.timezone)}</div>{f.body.slice(0, 240)}<div><button className="btn small" disabled={add.isPending || p.evidence.some((e) => e.chunk_id === f.chunk_ids[0])} onClick={() => add.mutate(f.chunk_ids[0]!)}>Als Beleg übernehmen</button></div></div>
      ))}
      {res.data && res.data.items.length === 0 ? <p className="muted">Keine Treffer.</p> : null}
      {add.isError ? <ErrorNotice error={add.error} /> : null}
    </Modal>
  );
}

function InitiativeModal({ open, onClose, p }: { open: boolean; onClose: () => void; p: Detail }) {
  const { path, tenantId } = useWorkspace();
  const router = useRouter();
  const [f, setF] = useState({ title: "", desired_outcome: "", target_segment: "", effort_low: "", effort_high: "", effort_unit: "person_days" });
  const m = useMutation({
    mutationFn: () => api<S["InitiativeDetail"]>("POST", path("/initiatives"), { body: { problem_id: p.id, title: f.title, desired_outcome: f.desired_outcome, target_segment: f.target_segment || null, effort_low: f.effort_low || null, effort_high: f.effort_high || null, effort_unit: f.effort_unit } }),
    onSuccess: (i) => router.push(`/w/${tenantId}/initiatives/${i.id}`),
  });
  return (
    <Modal open={open} onClose={onClose} title="Initiative anlegen" footer={<button className="btn primary" disabled={!f.title.trim() || m.isPending} onClick={() => m.mutate()} data-testid="initiative-create">Anlegen</button>}>
      <p className="small muted">Problem: {p.title}. Aufwände sind Schätzungen des Teams (keine KI-Werte); leer lassen, wenn unbekannt.</p>
      <Field id="i-title" label="Titel"><input id="i-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} data-testid="initiative-title" /></Field>
      <Field id="i-out" label="Gewünschtes Ergebnis"><textarea id="i-out" value={f.desired_outcome} onChange={(e) => setF({ ...f, desired_outcome: e.target.value })} /></Field>
      <Field id="i-seg" label="Zielsegment (optional)"><input id="i-seg" value={f.target_segment} onChange={(e) => setF({ ...f, target_segment: e.target.value })} /></Field>
      <div className="grid cols-3">
        <Field id="i-lo" label="Aufwand von"><input id="i-lo" type="number" min="0" step="0.5" value={f.effort_low} onChange={(e) => setF({ ...f, effort_low: e.target.value })} /></Field>
        <Field id="i-hi" label="Aufwand bis"><input id="i-hi" type="number" min="0" step="0.5" value={f.effort_high} onChange={(e) => setF({ ...f, effort_high: e.target.value })} /></Field>
        <Field id="i-un" label="Einheit"><select id="i-un" value={f.effort_unit} onChange={(e) => setF({ ...f, effort_unit: e.target.value })}><option value="person_days">Personentage</option><option value="person_weeks">Personenwochen</option></select></Field>
      </div>
      {m.isError ? <ErrorNotice error={m.error} /> : null}
    </Modal>
  );
}
