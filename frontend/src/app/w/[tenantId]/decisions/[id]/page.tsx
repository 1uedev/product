"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api, type S } from "@/lib/api";
import { fmtDate, fmtScore, locatorText } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, DemoBadge, ErrorNotice, Field, StatusBadge } from "@/components/ui";
import { MetricsPanel, type Metrics } from "@/components/metrics";
import { JobProgress } from "@/components/jobs";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type Doc = S["DecisionOut"];
interface Option { key?: string; name: string; description: string; effort_low: string; effort_high: string; effort_unit: string; addressed_segments: string; expected_effects: string; risks: string; risk_rating: string }
interface ScoreResult { name: string; total: string | null; weight_coverage: string; missing: string[]; contributions: { criterion: string; weight: string; effective_weight: string; value: string | null; points: string; status: string; note: string }[] }
interface Scoring { policy: { name: string; formula_version: string; weights: Record<string, string>; missing_value_policy: string }; results: ScoreResult[]; ranking: string[]; sensitivity: { stable: boolean | null; scenarios: { criterion: string; direction: string; ranking: string[]; ranking_changed: boolean }[] } | null }
interface Snapshot { taken_at: string; metrics: Metrics; evidence: { evidence_id: string; chunk_id: string; relation: string; quote: string; customer_name: string | null; segment: string | null; occurred_at: string; channel: string; locator: Record<string, unknown>; human_verified: boolean }[] }
interface Drift { has_new_knowledge: boolean; changes: { supporting_customers: number; supporting_statements: number; contradicting_statements: number }; new_evidence: { quote: string; relation: string; customer_name: string | null; occurred_at: string }[]; snapshot_taken_at: string }
interface AiDraft { summary: string; provider_label: string; claims: { statement: string; criterion: string; source_chunk_ids: string[]; result_ids: string[] }[]; open_questions: string[]; caveats: string[]; verification: { accepted_claims: number; rejected_claims: number } }

const blank = (): Option => ({ name: "", description: "", effort_low: "", effort_high: "", effort_unit: "person_days", addressed_segments: "", expected_effects: "", risks: "", risk_rating: "" });
const toForm = (o: Record<string, unknown>): Option => ({
  key: String(o.key ?? ""), name: String(o.name ?? ""), description: String(o.description ?? ""), effort_low: o.effort_low == null ? "" : String(o.effort_low), effort_high: o.effort_high == null ? "" : String(o.effort_high),
  effort_unit: String(o.effort_unit ?? "person_days"), addressed_segments: ((o.addressed_segments as string[]) ?? []).join(", "), expected_effects: String(o.expected_effects ?? ""), risks: String(o.risks ?? ""), risk_rating: String(o.risk_rating ?? ""),
});
const fromForm = (o: Option) => ({
  name: o.name, description: o.description, effort_low: o.effort_low || null, effort_high: o.effort_high || null, effort_unit: o.effort_unit,
  addressed_segments: o.addressed_segments.split(",").map((s) => s.trim()).filter(Boolean), expected_effects: o.expected_effects, risks: o.risks, risk_rating: o.risk_rating || null,
});

export default function DecisionPage() {
  const { id } = useParams<{ id: string }>();
  const { path, can, tenantId } = useWorkspace();
  const q = useQuery({ queryKey: ["decision", id], queryFn: () => api<Doc>("GET", path(`/decisions/${id}`)) });
  return <Async q={q}>{(d) => <Editor key={`${d.id}-${d.version}`} doc={d} id={id} tenantId={tenantId} canEdit={can("editor")} canReview={can("admin")} />}</Async>;
}

function Editor({ doc, id, tenantId, canEdit, canReview }: { doc: Doc; id: string; tenantId: string; canEdit: boolean; canReview: boolean }) {
  const { path, ws } = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const editable = doc.editable && canEdit;
  const [title, setTitle] = useState(doc.title);
  const [text, setText] = useState(doc.recommendation_text);
  const [opts, setOpts] = useState<Option[]>(doc.options.length ? doc.options.map(toForm) : [blank(), blank()]);
  const [aiJob, setAiJob] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const snap = (doc.evidence_snapshot as unknown as Snapshot | undefined)?.metrics ? (doc.evidence_snapshot as unknown as Snapshot) : null;
  const scoring = (doc.scoring_snapshot as unknown as Scoring | undefined)?.results ? (doc.scoring_snapshot as unknown as Scoring) : null;
  const drift = doc.drift as unknown as Drift | null;
  const ai = doc.ai_draft as unknown as AiDraft | null;
  const set = (d: Doc) => { qc.setQueryData(["decision", id], d); void qc.invalidateQueries({ queryKey: ["decisions"] }); };
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "Fehler", "error");
  const save = useMutation({
    mutationFn: () => api<Doc>("PATCH", path(`/decisions/${id}`), { body: { title, recommendation_text: text, options: opts.filter((o) => o.name.trim()).map(fromForm) }, version: doc.version }),
    onSuccess: (d) => { set(d); toast("Entwurf gespeichert.", "ok"); }, onError: fail,
  });
  const useAction = (name: string, ok: string) => useMutation({
    mutationFn: () => api<Doc>("POST", path(`/decisions/${id}/${name}`)), onSuccess: (d) => { set(d); toast(ok, "ok"); }, onError: fail,
  });
  const snapshot = useAction("snapshot", "Belegstand eingefroren.");
  const submit = useAction("submit", "Zur Prüfung eingereicht.");
  const approve = useAction("approve", "Freigegeben. Diese Revision ist jetzt unveränderlich.");
  const changes = useAction("request-changes", "Zurück in den Entwurf gesetzt.");
  const newRev = useMutation({ mutationFn: () => api<Doc>("POST", path(`/initiatives/${doc.initiative_id}/decisions`)), onSuccess: (d) => router.push(`/w/${tenantId}/decisions/${d.id}`), onError: fail });
  const addComment = useMutation({ mutationFn: () => api<Doc>("POST", path(`/decisions/${id}/comments`), { body: { body: comment } }), onSuccess: (d) => { set(d); setComment(""); }, onError: fail });
  const aiReq = useMutation({ mutationFn: () => api<S["RationaleAccepted"]>("POST", path(`/decisions/${id}/ai-draft`)), onSuccess: (r) => setAiJob(r.job.id), onError: fail });
  const dirty = title !== doc.title || text !== doc.recommendation_text || JSON.stringify(opts.filter((o) => o.name.trim()).map(fromForm)) !== JSON.stringify(doc.options.map((o) => fromForm(toForm(o))));
  const exportHref = (f: string) => path(`/decisions/${id}/export?format=${f}`);
  return (
    <>
      <div className="page-head">
        <div>
          <p className="small"><Link href={`/w/${tenantId}/decisions`}>← Entscheidungen</Link> · <Link href={`/w/${tenantId}/initiatives/${doc.initiative_id}`}>{doc.initiative_title}</Link></p>
          <h1 data-testid="decision-heading">{doc.title || "Entscheidungsvorlage"} <span className="muted">Revision {doc.revision}</span></h1>
          <div className="row"><span data-testid="decision-state"><StatusBadge value={doc.state} map={t.status.decision} /></span>
            {doc.approved_at ? <span className="small">Freigegeben von {doc.approved_by_name} am {fmtDate(doc.approved_at, ws.timezone, true)}</span> : null}
            {doc.created_by_name ? <span className="small muted">Autor: {doc.created_by_name}</span> : null}</div>
        </div>
        <div className="actions">
          <a className="btn" href={exportHref("md")} data-testid="export-md">Export Markdown</a>
          <a className="btn" href={exportHref("csv")} data-testid="export-csv">Export Belege (CSV)</a>
          <a className="btn" href={exportHref("scores_csv")}>Export Scores (CSV)</a>
        </div>
      </div>
      {doc.state === "approved" ? <div className="notice ok" role="note">Diese Revision ist freigegeben und unveränderlich. Der Belegstand zeigt den Wissensstand zum Zeitpunkt der Freigabe, nicht den heutigen.</div> : null}
      {doc.state === "superseded" ? <div className="notice warn" role="note">Diese Revision wurde durch eine neuere freigegebene Revision abgelöst. Sie bleibt als historische Entscheidung erhalten.</div> : null}
      {drift?.has_new_knowledge && doc.state !== "draft" ? (
        <div className="notice warn" role="status" data-testid="drift-notice">
          <strong>Aktueller Wissensstand weicht vom Belegstand ab.</strong> Seit {fmtDate(drift.snapshot_taken_at, ws.timezone, true)}: {drift.changes.supporting_customers >= 0 ? "+" : ""}{drift.changes.supporting_customers} Kunden, {drift.changes.supporting_statements >= 0 ? "+" : ""}{drift.changes.supporting_statements} unterstützende und {drift.changes.contradicting_statements >= 0 ? "+" : ""}{drift.changes.contradicting_statements} widersprechende Aussagen.
          {drift.new_evidence.length ? <ul>{drift.new_evidence.map((e, i) => <li key={i}>{t.relation[e.relation]}: „{e.quote.slice(0, 120)}“ ({e.customer_name ?? "ohne Kunde"}, {fmtDate(e.occurred_at, ws.timezone)})</li>)}</ul> : null}
          {doc.state === "approved" && canEdit ? <button className="btn small" onClick={() => newRev.mutate()} disabled={newRev.isPending} data-testid="new-revision">Neue Revision auf Basis des aktuellen Stands anlegen</button> : null}
        </div>
      ) : null}
      {doc.state === "approved" && canEdit && !drift?.has_new_knowledge ? <div className="row" style={{ marginBottom: ".75rem" }}><button className="btn" onClick={() => newRev.mutate()} disabled={newRev.isPending}>Neue Revision anlegen</button></div> : null}

      <div className="stack">
        <section className="card" aria-labelledby="opts">
          <h2 id="opts">1. Handlungsoptionen</h2>
          {editable ? <Field id="d-title" label="Titel"><input id="d-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} data-testid="decision-title" /></Field> : null}
          <div className="grid cols-2">
            {(editable ? opts : doc.options.map(toForm)).map((o, idx) => (
              <div className="card" key={idx} data-testid={`option-${idx}`}>
                <h3>Option {String.fromCharCode(65 + idx)}{!editable ? `: ${o.name}` : ""}</h3>
                {editable ? (
                  <>
                    <Field id={`o${idx}-n`} label="Name"><input id={`o${idx}-n`} value={o.name} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, name: e.target.value } : x)))} data-testid={`option-name-${idx}`} /></Field>
                    <Field id={`o${idx}-d`} label="Beschreibung"><textarea id={`o${idx}-d`} rows={3} value={o.description} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, description: e.target.value } : x)))} /></Field>
                    <div className="grid cols-3">
                      <Field id={`o${idx}-l`} label="Aufwand von"><input id={`o${idx}-l`} type="number" min="0" step="0.5" value={o.effort_low} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, effort_low: e.target.value } : x)))} data-testid={`option-low-${idx}`} /></Field>
                      <Field id={`o${idx}-h`} label="Aufwand bis"><input id={`o${idx}-h`} type="number" min="0" step="0.5" value={o.effort_high} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, effort_high: e.target.value } : x)))} /></Field>
                      <Field id={`o${idx}-u`} label="Einheit"><select id={`o${idx}-u`} value={o.effort_unit} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, effort_unit: e.target.value } : x)))}><option value="person_days">Personentage</option><option value="person_weeks">Personenwochen</option></select></Field>
                    </div>
                    <Field id={`o${idx}-s`} label="Adressierte Segmente (kommagetrennt, leer = alle)"><input id={`o${idx}-s`} value={o.addressed_segments} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, addressed_segments: e.target.value } : x)))} placeholder="enterprise, smb" /></Field>
                    <Field id={`o${idx}-e`} label="Erwartete Wirkung (qualitativ, keine Prognose)"><textarea id={`o${idx}-e`} rows={2} value={o.expected_effects} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, expected_effects: e.target.value } : x)))} /></Field>
                    <Field id={`o${idx}-r`} label="Risiken"><textarea id={`o${idx}-r`} rows={2} value={o.risks} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, risks: e.target.value } : x)))} /></Field>
                    <Field id={`o${idx}-rr`} label="Risikoeinschätzung (Mensch)"><select id={`o${idx}-rr`} value={o.risk_rating} onChange={(e) => setOpts(opts.map((x, i) => (i === idx ? { ...x, risk_rating: e.target.value } : x)))}><option value="">nicht eingeschätzt</option><option value="low">niedrig</option><option value="medium">mittel</option><option value="high">hoch</option></select></Field>
                  </>
                ) : (
                  <dl className="kv"><dt>Beschreibung</dt><dd>{o.description || "–"}</dd><dt>Aufwand</dt><dd>{o.effort_low || "?"} – {o.effort_high || "?"} {o.effort_unit === "person_weeks" ? "Personenwochen" : "Personentage"}</dd><dt>Segmente</dt><dd>{o.addressed_segments || "alle"}</dd><dt>Wirkung</dt><dd>{o.expected_effects || "–"}</dd><dt>Risiken</dt><dd>{o.risks || "–"}</dd><dt>Risiko (Mensch)</dt><dd>{o.risk_rating || "–"}</dd></dl>
                )}
              </div>
            ))}
          </div>
          {editable && opts.length < 4 ? <button className="btn small" style={{ marginTop: ".5rem" }} onClick={() => setOpts([...opts, blank()])}>Option hinzufügen</button> : null}
        </section>

        <section className="card" aria-labelledby="ev">
          <div className="row"><h2 id="ev" style={{ margin: 0 }}>2. Belegstand und Scoring</h2><span className="spacer" />
            {editable ? <button className="btn" disabled={snapshot.isPending || dirty} title={dirty ? "Bitte zuerst speichern" : undefined} onClick={() => snapshot.mutate()} data-testid="snapshot-refresh">{snap ? "Belegstand aktualisieren" : "Belegstand einfrieren"}</button> : null}</div>
          {dirty && editable ? <p className="small muted">Ungespeicherte Änderungen: bitte speichern, bevor der Belegstand aktualisiert wird (Optionen fließen in das Scoring ein).</p> : null}
          {snap ? (
            <div className="stack">
              <p className="small muted">Eingefroren am {fmtDate(snap.taken_at, ws.timezone, true)}. {snap.evidence.length} Belege, jeder mit Originalzitat und Fundstelle.</p>
              <MetricsPanel m={snap.metrics} compact />
              {scoring ? (
                <div className="card" data-testid="scoring-card">
                  <h3>Score-Vergleich der Optionen</h3>
                  <p className="small muted">Policy „{scoring.policy.name}“ ({scoring.policy.formula_version}), fehlende Werte: {scoring.policy.missing_value_policy}. Ranking: {scoring.ranking.join(" > ") || "–"}</p>
                  <div className="table-wrap"><table><thead><tr><th>Kriterium</th>{scoring.results.map((r) => <th key={r.name} className="num">{r.name}</th>)}</tr></thead><tbody>
                    {Object.keys(t.criteria).filter((k) => scoring.results.some((r) => r.contributions.some((c) => c.criterion === k))).map((k) => (
                      <tr key={k}><td>{t.criteria[k]}</td>{scoring.results.map((r) => { const c = r.contributions.find((x) => x.criterion === k); return <td key={r.name} className="num" title={c?.note}>{c?.value ?? "unbekannt"}<div className="small muted">{c?.points} P.</div></td>; })}</tr>))}
                    <tr><th>Gesamt</th>{scoring.results.map((r) => <th key={r.name} className="num">{fmtScore(r.total)}</th>)}</tr>
                    <tr><td>Datenabdeckung</td>{scoring.results.map((r) => <td key={r.name} className="num">{Math.round(Number(r.weight_coverage) * 100)} %</td>)}</tr>
                  </tbody></table></div>
                  {scoring.sensitivity ? <p role="status">{scoring.sensitivity.stable ? "Die Rangfolge ist bei ±25 % Gewichtsänderung stabil." : "Die Rangfolge ist bei ±25 % Gewichtsänderung nicht stabil."} <span className="small muted">({scoring.sensitivity.scenarios.filter((s) => s.ranking_changed).length} von {scoring.sensitivity.scenarios.length} Szenarien ändern sie)</span></p> : null}
                </div>
              ) : null}
              <details><summary>Belege im Snapshot ({snap.evidence.length})</summary>
                {snap.evidence.map((e) => <div key={e.evidence_id} className={`quote ${e.relation}`}><div className="small muted">{t.relation[e.relation]} · {e.customer_name ?? "ohne Kunde"} · {fmtDate(e.occurred_at, ws.timezone)} · {locatorText(e.locator)}</div>„{e.quote}“</div>)}
              </details>
            </div>
          ) : <p className="muted">Noch kein eingefrorener Belegstand. Er ist Voraussetzung für Prüfung, Export und KI-Entwurf.</p>}
          {snapshot.isError ? <ErrorNotice error={snapshot.error} /> : null}
        </section>

        <section className="card" aria-labelledby="rec">
          <h2 id="rec">3. Begründung des Teams</h2>
          {editable ? <Field id="d-rec" label="Empfehlung und Begründung (menschlicher Text)" hint="Die KI trifft keine Entscheidung. Dieser Text ist Ihre Begründung."><textarea id="d-rec" rows={6} value={text} onChange={(e) => setText(e.target.value)} data-testid="decision-text" /></Field> : <p style={{ whiteSpace: "pre-wrap" }}>{doc.recommendation_text || "–"}</p>}
          {editable ? (
            <div className="row">
              <button className="btn primary" onClick={() => save.mutate()} disabled={save.isPending || !dirty} data-testid="decision-save">Entwurf speichern</button>
              <button className="btn" onClick={() => submit.mutate()} disabled={submit.isPending || dirty || !snap} data-testid="decision-submit" title={!snap ? "Zuerst den Belegstand einfrieren" : dirty ? "Zuerst speichern" : undefined}>Zur Prüfung einreichen</button>
            </div>
          ) : null}
          {submit.isError ? <ErrorNotice error={submit.error} /> : null}
          {save.isError ? <ErrorNotice error={save.error} /> : null}
        </section>

        <section className="card" aria-labelledby="ai">
          <div className="row"><h2 id="ai" style={{ margin: 0 }}>KI-Entwurf zur Begründung</h2>{doc.ai_run?.demo ? <DemoBadge /> : null}<span className="spacer" />
            {editable && snap ? <button className="btn" onClick={() => aiReq.mutate()} disabled={aiReq.isPending} data-testid="ai-draft-request">{ai ? "Neuen KI-Entwurf anfordern" : "KI-Entwurf anfordern"}</button> : null}</div>
          <p className="small muted">Der Entwurf fasst nur den eingefrorenen Belegstand und berechnete Kennzahlen zusammen. Jede Aussage verweist auf geprüfte Belege oder Ergebnis-IDs. Er ist kein Teil der Entscheidung.</p>
          {aiReq.isError ? <ErrorNotice error={aiReq.error} /> : null}
          {aiJob ? <JobProgress jobId={aiJob} label="KI-Entwurf" onDone={() => void qc.invalidateQueries({ queryKey: ["decision", id] })} /> : null}
          {ai ? (
            <div data-testid="ai-draft"><div className="notice"><strong>{ai.provider_label}</strong><p>{ai.summary}</p></div>
              <ul>{ai.claims.map((c, i) => <li key={i}>{c.statement} <span className="small muted">({c.source_chunk_ids.length} Belege, {c.result_ids.length} Kennzahlen)</span></li>)}</ul>
              {ai.open_questions.length ? <><h3>Offene Fragen</h3><ul>{ai.open_questions.map((x) => <li key={x}>{x}</li>)}</ul></> : null}
              {ai.caveats.length ? <><h3>Grenzen</h3><ul>{ai.caveats.map((x) => <li key={x}>{x}</li>)}</ul></> : null}
              <p className="small muted">Geprüft: {ai.verification.accepted_claims} Aussagen übernommen, {ai.verification.rejected_claims} wegen fehlender Belege verworfen.</p>
              {editable ? <button className="btn small" onClick={() => setText((x) => `${x}${x ? "\n\n" : ""}${ai.summary}`)}>Zusammenfassung in die Begründung übernehmen</button> : null}
            </div>
          ) : <p className="muted">Noch kein KI-Entwurf.</p>}
        </section>

        <section className="card" aria-labelledby="wf">
          <h2 id="wf">Prüfung und Freigabe</h2>
          {doc.state === "in_review" ? (
            canReview ? (
              <div className="row">
                <button className="btn primary" disabled={!doc.can_approve || approve.isPending} onClick={() => approve.mutate()} data-testid="decision-approve">Freigeben</button>
                <button className="btn" disabled={changes.isPending} onClick={() => changes.mutate()} data-testid="decision-changes">Änderungen anfordern</button>
                {!doc.can_approve ? <span className="small muted">{doc.approve_block_reason}</span> : null}
              </div>
            ) : <p className="muted">Wartet auf Freigabe durch einen Admin oder Owner (Vier-Augen-Prinzip).</p>
          ) : <p className="muted">{doc.state === "draft" ? "Im Entwurf: erst einreichen, dann kann eine andere Person freigeben." : "Abgeschlossen."}</p>}
          {approve.isError ? <ErrorNotice error={approve.error} /> : null}
        </section>

        <section className="card" aria-labelledby="com">
          <h2 id="com">Kommentare</h2>
          {doc.comments.length === 0 ? <p className="muted">Keine Kommentare.</p> : <ul data-testid="comments">{doc.comments.map((c) => <li key={c.id}><strong>{c.author_name}</strong> <span className="small muted">{fmtDate(c.created_at, ws.timezone, true)}</span><div>{c.body}</div></li>)}</ul>}
          {canEdit ? <form onSubmit={(e) => { e.preventDefault(); if (comment.trim()) addComment.mutate(); }}><Field id="c-body" label="Kommentar"><textarea id="c-body" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} data-testid="comment-body" /></Field><button className="btn" type="submit" disabled={addComment.isPending || !comment.trim()} data-testid="comment-add">Kommentieren</button></form> : null}
        </section>

        <section className="card" aria-labelledby="rev">
          <h2 id="rev">Revisionen</h2>
          <ul>{doc.revisions.map((r) => <li key={String(r.id)}>{String(r.id) === id ? <strong>Revision {String(r.revision)} (aktuell geöffnet)</strong> : <Link href={`/w/${tenantId}/decisions/${String(r.id)}`}>Revision {String(r.revision)}</Link>} <StatusBadge value={String(r.state)} map={t.status.decision} /> {r.approved_at ? <span className="small muted">freigegeben {fmtDate(String(r.approved_at), ws.timezone)}</span> : null}</li>)}</ul>
        </section>
      </div>
    </>
  );
}

