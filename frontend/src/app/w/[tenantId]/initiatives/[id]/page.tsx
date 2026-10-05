"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api, type S } from "@/lib/api";
import { fmtDate, fmtScore } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Badge, ErrorNotice, Field, Modal, StatusBadge } from "@/components/ui";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type Detail = S["InitiativeDetail"];
interface Score { total: string | null; weight_coverage: string; missing: string[]; blocked: boolean; contributions: { criterion: string; weight: string; effective_weight: string; value: string | null; points: string; note: string; status: string }[]; policy: { name: string } }

export default function InitiativePage() {
  const { id } = useParams<{ id: string }>();
  const { path, can, ws, tenantId } = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const q = useQuery({ queryKey: ["initiative", id], queryFn: () => api<Detail>("GET", path(`/initiatives/${id}`)) });
  const [aOpen, setAOpen] = useState(false);
  const set = (d: Detail) => { qc.setQueryData(["initiative", id], d); void qc.invalidateQueries({ queryKey: ["initiatives"] }); };
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Detail>("PATCH", path(`/initiatives/${id}`), { body, version: q.data!.version }),
    onSuccess: (d) => { set(d); toast("Gespeichert.", "ok"); }, onError: (e) => toast(e instanceof Error ? e.message : "Fehler", "error"),
  });
  const assumption = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) => api<Detail>("PATCH", path(`/assumptions/${v.id}`), { body: v.body }), onSuccess: set,
  });
  const delAssumption = useMutation({ mutationFn: (aid: string) => api<Detail>("DELETE", path(`/assumptions/${aid}`)), onSuccess: set });
  const newDecision = useMutation({
    mutationFn: () => api<S["DecisionOut"]>("POST", path(`/initiatives/${id}/decisions`)),
    onSuccess: (d) => router.push(`/w/${tenantId}/decisions/${d.id}`), onError: (e) => toast(e instanceof Error ? e.message : "Fehler", "error"),
  });
  const editor = can("editor");
  return (
    <Async q={q}>
      {(i) => {
        const score = i.score as unknown as Score | null;
        const open = i.decisions.find((d) => d.state === "draft" || d.state === "in_review");
        return (
          <>
            <div className="page-head">
              <div>
                <p className="small"><Link href={`/w/${tenantId}/initiatives`}>← Initiativen</Link></p>
                <h1 data-testid="initiative-heading">{i.title}</h1>
                <div className="row"><StatusBadge value={i.status} map={t.status.initiative} /><span className="small muted">Problem: <Link href={`/w/${tenantId}/problems/${i.problem_id}`}>{i.problem_title}</Link></span></div>
                {i.desired_outcome ? <p>{i.desired_outcome}</p> : null}
              </div>
              {editor ? <div className="actions">
                {open ? <Link className="btn primary" href={`/w/${tenantId}/decisions/${String(open.id)}`} data-testid="open-decision">Offene Entscheidungsvorlage öffnen</Link>
                  : <button className="btn primary" onClick={() => newDecision.mutate()} disabled={newDecision.isPending} data-testid="create-decision">{i.decisions.length ? "Neue Revision der Entscheidungsvorlage" : "Entscheidungsvorlage erstellen"}</button>}
                <select aria-label="Status ändern" value={i.status} onChange={(e) => patch.mutate({ status: e.target.value })} style={{ width: "auto" }}>{Object.entries(t.status.initiative).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              </div> : null}
            </div>
            <div className="grid cols-2">
              <section className="card" aria-labelledby="ass">
                <div className="row"><h2 id="ass" style={{ margin: 0 }}>Annahmen</h2><span className="spacer" />{editor ? <button className="btn small" onClick={() => setAOpen(true)} data-testid="add-assumption">Annahme hinzufügen</button> : null}</div>
                <p className="small muted">Beobachtet = mit Quelle belegt, Schätzung und Hypothese sind Annahmen des Teams. Die KI erfindet keine Aufwände oder Erfolgswahrscheinlichkeiten.</p>
                {i.assumptions.length === 0 ? <p className="muted">Noch keine Annahmen.</p> : (
                  <ul style={{ padding: 0, listStyle: "none" }}>{i.assumptions.map((a) => (
                    <li key={a.id} className="quote">
                      <div className="row small"><Badge kind={a.kind === "observed" ? "ok" : "info"}>{{ observed: "beobachtet", estimate: "Schätzung", hypothesis: "Hypothese" }[a.kind]}</Badge>
                        {editor ? <select aria-label="Validierungsstatus" style={{ width: "auto" }} value={a.validation_status} onChange={(e) => assumption.mutate({ id: a.id, body: { validation_status: e.target.value } })}><option value="open">offen</option><option value="validated">bestätigt</option><option value="refuted">widerlegt</option></select> : <Badge>{a.validation_status}</Badge>}
                        {editor ? <button className="btn link small" onClick={() => delAssumption.mutate(a.id)}>entfernen</button> : null}</div>
                      <div>{a.statement}</div>
                      {a.source_quote ? <div className="small muted">Quelle: „{a.source_quote}“</div> : null}
                    </li>))}</ul>
                )}
              </section>
              <section className="card" aria-labelledby="eff">
                <h2 id="eff">Aufwand und Score-Vorschau</h2>
                <dl className="kv"><dt>Aufwand (Schätzung)</dt><dd>{i.effort_low ?? "?"} – {i.effort_high ?? "?"} {i.effort_unit === "person_weeks" ? "Personenwochen" : "Personentage"}</dd><dt>Zielsegment</dt><dd>{i.target_segment ?? "–"}</dd><dt>Entscheidungsrevisionen</dt><dd>{i.decisions.length ? i.decisions.map((d) => <span key={String(d.id)} style={{ marginRight: ".5rem" }}><Link href={`/w/${tenantId}/decisions/${String(d.id)}`}>R{String(d.revision)}</Link> <StatusBadge value={String(d.state)} map={t.status.decision} /></span>) : "keine"}</dd></dl>
                {score ? (
                  <div style={{ marginTop: ".75rem" }}>
                    <div className="row"><strong>Score:</strong> <span style={{ fontSize: "1.3rem", fontWeight: 700 }} data-testid="initiative-score">{fmtScore(score.total)}</span><span className="small muted">Policy „{score.policy.name}“, Datenabdeckung der Gewichte {Math.round(Number(score.weight_coverage) * 100)} %</span></div>
                    {score.missing.length ? <div className="notice warn small">Für diese Kriterien fehlen Werte (nicht als 0 gerechnet): {score.missing.map((m) => t.criteria[m] ?? m).join(", ")}.</div> : null}
                    <div className="table-wrap"><table><thead><tr><th>Kriterium</th><th className="num">Gewicht</th><th className="num">Wert</th><th className="num">Punkte</th></tr></thead><tbody>
                      {score.contributions.map((c) => <tr key={c.criterion}><td>{t.criteria[c.criterion]}<div className="small muted">{c.note}</div></td><td className="num">{c.effective_weight}</td><td className="num">{c.value ?? "unbekannt"}</td><td className="num">{c.points}</td></tr>)}
                    </tbody></table></div>
                    <p className="small"><Link href={`/w/${tenantId}/compare?ids=${i.id}`}>Mit anderen Initiativen vergleichen</Link></p>
                  </div>
                ) : null}
              </section>
            </div>
            <section className="card" style={{ marginTop: "1rem" }}><h2>Problemkontext</h2><p>{String((i.problem as Record<string, unknown>).unique_customers)} eindeutige Kunden, {String((i.problem as Record<string, unknown>).statements)} Aussagen. Aktualisiert {fmtDate(i.updated_at, ws.timezone, true)}.</p></section>
            <AssumptionModal open={aOpen} onClose={() => setAOpen(false)} initiative={i} onDone={(d) => { set(d); setAOpen(false); }} />
          </>
        );
      }}
    </Async>
  );
}

function AssumptionModal({ open, onClose, initiative, onDone }: { open: boolean; onClose: () => void; initiative: Detail; onDone: (d: Detail) => void }) {
  const { path } = useWorkspace();
  const [f, setF] = useState({ statement: "", kind: "hypothesis", source_chunk_id: "" });
  const problem = useQuery({ queryKey: ["problem", initiative.problem_id], enabled: open && f.kind === "observed", queryFn: () => api<S["ProblemDetail"]>("GET", path(`/problems/${initiative.problem_id}`)) });
  const m = useMutation({
    mutationFn: () => api<Detail>("POST", path(`/initiatives/${initiative.id}/assumptions`), { body: { statement: f.statement, kind: f.kind, source_chunk_id: f.kind === "observed" ? f.source_chunk_id : null } }),
    onSuccess: (d) => { setF({ statement: "", kind: "hypothesis", source_chunk_id: "" }); onDone(d); },
  });
  return (
    <Modal open={open} onClose={onClose} title="Annahme hinzufügen" footer={<button className="btn primary" disabled={!f.statement.trim() || (f.kind === "observed" && !f.source_chunk_id) || m.isPending} onClick={() => m.mutate()} data-testid="assumption-save">Hinzufügen</button>}>
      <Field id="a-st" label="Aussage"><textarea id="a-st" value={f.statement} onChange={(e) => setF({ ...f, statement: e.target.value })} data-testid="assumption-statement" /></Field>
      <Field id="a-kind" label="Art"><select id="a-kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} data-testid="assumption-kind"><option value="hypothesis">Hypothese</option><option value="estimate">Schätzung</option><option value="observed">Beobachtet (mit Quelle)</option></select></Field>
      {f.kind === "observed" ? <Field id="a-src" label="Quelle (Beleg des Problems)"><select id="a-src" value={f.source_chunk_id} onChange={(e) => setF({ ...f, source_chunk_id: e.target.value })}><option value="">Beleg wählen …</option>{problem.data?.evidence.map((e) => <option key={e.chunk_id} value={e.chunk_id}>{e.quote.slice(0, 80)}</option>)}</select></Field> : null}
      {m.isError ? <ErrorNotice error={m.error} /> : null}
    </Modal>
  );
}
