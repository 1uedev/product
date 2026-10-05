"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, DemoBadge, ErrorNotice, Field, Modal, Pager, StatusBadge } from "@/components/ui";
import { JobProgress } from "@/components/jobs";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type Item = S["ProblemListItem"];

export default function ProblemsPage() {
  const { path, can, ws, tenantId } = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [f, setF] = useState({ status: "", q: "", origin: "", sort: "customers", archived: false });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [job, setJob] = useState<string | null>(null);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [newForm, setNewForm] = useState({ title: "", description: "" });
  const list = useQuery({
    queryKey: ["problems", tenantId, f, offset],
    queryFn: () => api<Page<Item>>("GET", path("/problems"), { query: { status: f.status, q: f.q, origin: f.origin, sort: f.sort, include_archived: f.archived, limit: 25, offset } }),
  });
  const analysis = useMutation({
    mutationFn: (scope: string) => api<S["AnalysisAccepted"]>("POST", path("/analysis"), { body: { scope } }),
    onSuccess: (r) => { setJob(r.job.id); if (r.truncated) toast("Es wurden nur die neuesten Belege berücksichtigt (Kontextlimit).", "info"); },
  });
  const merge = useMutation({
    mutationFn: () => api<S["ProblemDetail"]>("POST", path("/problems/merge"), { body: { target_id: target, source_ids: [...selected].filter((x) => x !== target) } }),
    onSuccess: (p) => { toast("Probleme zusammengeführt.", "ok"); setMergeOpen(false); setSelected(new Set()); void qc.invalidateQueries({ queryKey: ["problems"] }); router.push(`/w/${tenantId}/problems/${p.id}`); },
  });
  const create = useMutation({
    mutationFn: () => api<S["ProblemDetail"]>("POST", path("/problems"), { body: newForm }),
    onSuccess: (p) => router.push(`/w/${tenantId}/problems/${p.id}`),
  });
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <>
      <div className="page-head">
        <div><h1>Probleme</h1><p className="muted">Häufigkeit zählt eindeutige Kunden; die Zahl der Aussagen steht daneben. Vorschläge der Analyse sind Hypothesen, die Sie prüfen und korrigieren.</p></div>
        {can("editor") ? <div className="actions">
          <button className="btn primary" onClick={() => analysis.mutate("unassigned")} disabled={analysis.isPending} data-testid="start-analysis">Analyse starten</button>
          <button className="btn" onClick={() => setNewOpen(true)}>Neues Problem</button>
        </div> : null}
      </div>
      {ws.ai.demo ? <div className="notice warn small" role="note">Die Analyse nutzt den <DemoBadge /> (lexikalisches Clustering der importierten Texte). Qualität und Titel sind nicht mit einem echten Sprachmodell vergleichbar.</div> : null}
      {analysis.isError ? <ErrorNotice error={analysis.error} /> : null}
      {job ? <div style={{ margin: ".5rem 0" }}><JobProgress jobId={job} label="Analyse" onDone={() => void qc.invalidateQueries({ queryKey: ["problems"] })} /></div> : null}
      <form className="card" onSubmit={(e) => e.preventDefault()} aria-label="Filter">
        <div className="grid cols-4">
          <Field id="pq" label="Titel enthält"><input id="pq" type="search" value={f.q} onChange={(e) => { setF({ ...f, q: e.target.value }); setOffset(0); }} /></Field>
          <Field id="ps" label="Status"><select id="ps" value={f.status} onChange={(e) => { setF({ ...f, status: e.target.value }); setOffset(0); }}><option value="">aktive</option>{Object.entries(t.status.problem).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field id="po" label="Herkunft"><select id="po" value={f.origin} onChange={(e) => { setF({ ...f, origin: e.target.value }); setOffset(0); }}><option value="">alle</option><option value="ai">Analyse</option><option value="manual">manuell</option><option value="split">abgespalten</option></select></Field>
          <Field id="pso" label="Sortierung"><select id="pso" value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })}><option value="customers">eindeutige Kunden</option><option value="statements">Aussagen</option><option value="updated">zuletzt geändert</option><option value="title">Titel</option></select></Field>
        </div>
      </form>
      {can("editor") && selected.size >= 2 ? <div className="notice row" role="status"><span>{selected.size} Probleme ausgewählt</span><button className="btn small" onClick={() => { setTarget([...selected][0] ?? ""); setMergeOpen(true); }} data-testid="merge-open">Zusammenführen …</button><button className="btn small" onClick={() => setSelected(new Set())}>Auswahl aufheben</button></div> : null}
      <Async q={list} isEmpty={(d) => d.items.length === 0} empty={<><p>Noch keine Probleme.</p>{can("editor") ? <p>Importieren Sie Feedback und starten Sie die Analyse.</p> : null}</>}>
        {(d) => (
          <div className="card" style={{ marginTop: "1rem" }}><div className="table-wrap"><table data-testid="problem-table">
            <thead><tr>{can("editor") ? <th><span className="sr-only">Auswahl</span></th> : null}<th>Problem</th><th>Status</th><th className="num">Kunden</th><th className="num">Aussagen</th><th className="num">Widerspr.</th><th className="num">verifiziert</th><th>Letzter Beleg</th><th className="num">Initiativen</th></tr></thead>
            <tbody>{d.items.map((p) => (
              <tr key={p.id} aria-selected={selected.has(p.id)}>
                {can("editor") ? <td><input type="checkbox" aria-label={`${p.title} auswählen`} checked={selected.has(p.id)} onChange={() => toggle(p.id)} /></td> : null}
                <td><Link href={`/w/${tenantId}/problems/${p.id}`}>{p.title}</Link> {p.demo_ai ? <DemoBadge /> : null}</td>
                <td><StatusBadge value={p.status} map={t.status.problem} /></td>
                <td className="num"><strong>{p.supporting_customers}</strong></td><td className="num">{p.supporting_statements}</td><td className="num">{p.contradicting_statements}</td><td className="num">{p.verified_evidence}</td>
                <td>{fmtDate(p.latest_evidence_at, ws.timezone)}</td><td className="num">{p.initiative_count}</td>
              </tr>))}</tbody></table></div>
            <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
            <label className="check small" style={{ marginTop: ".5rem" }}><input type="checkbox" checked={f.archived} onChange={(e) => setF({ ...f, archived: e.target.checked })} /> archivierte anzeigen</label></div>
        )}
      </Async>
      <Modal open={mergeOpen} onClose={() => setMergeOpen(false)} title="Probleme zusammenführen" footer={<button className="btn primary" disabled={merge.isPending} onClick={() => merge.mutate()} data-testid="merge-confirm">Zusammenführen</button>}>
        <p>Belege und Initiativen der übrigen Probleme wandern in das Zielproblem. Die übrigen Probleme werden archiviert und bleiben mit Verweis erhalten.</p>
        <Field id="mt" label="Zielproblem"><select id="mt" value={target} onChange={(e) => setTarget(e.target.value)}>{list.data?.items.filter((i) => selected.has(i.id)).map((i) => <option key={i.id} value={i.id}>{i.title}</option>)}</select></Field>
        {merge.isError ? <ErrorNotice error={merge.error} /> : null}
      </Modal>
      <Modal open={newOpen} onClose={() => setNewOpen(false)} title="Neues Problem" footer={<button className="btn primary" disabled={!newForm.title || create.isPending} onClick={() => create.mutate()}>Anlegen</button>}>
        <Field id="np-t" label="Titel"><input id="np-t" value={newForm.title} onChange={(e) => setNewForm({ ...newForm, title: e.target.value })} maxLength={300} /></Field>
        <Field id="np-d" label="Beschreibung"><textarea id="np-d" value={newForm.description} onChange={(e) => setNewForm({ ...newForm, description: e.target.value })} /></Field>
        {create.isError ? <ErrorNotice error={create.error} /> : null}
      </Modal>
    </>
  );
}
