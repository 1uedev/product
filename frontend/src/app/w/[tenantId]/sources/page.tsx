"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Fragment, useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate, locatorText } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Badge, ErrorNotice, Field, Pager, Tabs } from "@/components/ui";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type Feedback = S["FeedbackOut"];
type SourceDetail = S["SourceDetail"];

function Highlight({ text, query }: { text: string; query: string }) {
  const words = query.split(/\s+/).filter((w) => w.length > 2).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return <>{text}</>;
  const parts = text.split(new RegExp(`(${words.join("|")})`, "gi"));
  return <>{parts.map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>))}</>;
}

export default function SourcesPage() {
  const [tab, setTab] = useState("search");
  const [sourceId, setSourceId] = useState<string | null>(null);
  return (
    <>
      <div className="page-head"><div><h1>Quellen & Suche</h1><p className="muted">Volltextsuche im importierten Feedback und Ansicht der Originalquellen mit Fundstellen.</p></div></div>
      <Tabs tabs={[{ id: "search", label: "Feedback durchsuchen" }, { id: "sources", label: "Quellen" }]} value={tab} onChange={setTab} />
      <div className="split">
        <div>{tab === "search" ? <Search onOpen={setSourceId} selected={sourceId} /> : <SourceList onOpen={setSourceId} selected={sourceId} />}</div>
        <aside className="panel" aria-label="Quelle"><SourcePanel id={sourceId} onClose={() => setSourceId(null)} /></aside>
      </div>
    </>
  );
}

function Search({ onOpen, selected }: { onOpen: (id: string) => void; selected: string | null }) {
  const { path, ws, tenantId } = useWorkspace();
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState({ q: "", channel: "", assigned: "" });
  const [channel, setChannel] = useState("");
  const [assigned, setAssigned] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["feedback", tenantId, applied, offset],
    queryFn: () => api<Page<Feedback>>("GET", path("/feedback"), { query: { q: applied.q, channel: applied.channel, assigned: applied.assigned, limit: 20, offset } }),
  });
  return (
    <div className="stack">
      <form className="card" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied({ q, channel, assigned }); }}>
        <div className="grid cols-3">
          <Field id="fq" label="Suchbegriff"><input id="fq" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="z. B. SSO, Export, Rechnung" data-testid="feedback-q" /></Field>
          <Field id="fch" label="Kanal"><select id="fch" value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">alle</option>{Object.entries(t.channel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field id="fas" label="Zuordnung"><select id="fas" value={assigned} onChange={(e) => setAssigned(e.target.value)}><option value="">alle</option><option value="true">einem Problem zugeordnet</option><option value="false">noch nicht zugeordnet</option></select></Field>
        </div>
        <div className="row"><button className="btn primary" type="submit">{t.common.search}</button><button type="button" className="btn" onClick={() => { setQ(""); setChannel(""); setAssigned(""); setApplied({ q: "", channel: "", assigned: "" }); setOffset(0); }}>{t.common.reset}</button></div>
      </form>
      <Async q={query} isEmpty={(d) => d.items.length === 0} empty="Keine Treffer.">
        {(d) => (
          <div className="card">
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }} data-testid="feedback-list">
              {d.items.map((f) => (
                <li key={f.id} style={{ borderBottom: "1px solid var(--border)", padding: ".6rem 0" }} aria-current={selected === f.source_record_id}>
                  <div className="small muted">{fmtDate(f.occurred_at, ws.timezone)} · {t.channel[f.channel] ?? f.channel} · {f.customer_name ? <Link href={`/w/${tenantId}/customers?id=${f.customer_id}`}>{f.customer_name}</Link> : "ohne Kunde"} {f.problem_ids.length ? <Badge kind="ok">{f.problem_ids.length} Problem(e)</Badge> : <Badge>nicht zugeordnet</Badge>}</div>
                  <div><Highlight text={f.body} query={applied.q} /></div>
                  <button className="btn link small" onClick={() => onOpen(f.source_record_id)}>Quelle und Fundstellen anzeigen</button>
                </li>
              ))}
            </ul>
            <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
          </div>
        )}
      </Async>
    </div>
  );
}

function SourceList({ onOpen, selected }: { onOpen: (id: string) => void; selected: string | null }) {
  const { path, ws } = useWorkspace();
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ["sources", offset], queryFn: () => api<Page<S["SourceOut"]>>("GET", path("/sources"), { query: { limit: 20, offset } }) });
  return (
    <Async q={q} isEmpty={(d) => d.items.length === 0} empty="Noch keine Quellen importiert.">
      {(d) => (
        <div className="card"><div className="table-wrap"><table><thead><tr><th>Titel</th><th>Art</th><th>Herkunft</th><th>Datei</th><th className="num">Fundstellen</th><th>Datum</th></tr></thead><tbody>
          {d.items.map((s) => (
            <tr key={s.id} aria-selected={selected === s.id}><td><button className="btn link" onClick={() => onOpen(s.id)}>{s.title}</button></td><td>{s.source_kind}</td><td><Badge kind={s.origin === "synthetic" ? "warn" : undefined}>{t.origin[s.origin] ?? s.origin}</Badge></td><td>{s.file_name ?? "–"}</td><td className="num">{s.chunk_count}</td><td>{fmtDate(s.source_timestamp ?? s.created_at, ws.timezone)}</td></tr>
          ))}</tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>
      )}
    </Async>
  );
}

function SourcePanel({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { path, can, tenantId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["source", id], enabled: !!id, queryFn: () => api<SourceDetail>("GET", path(`/sources/${id}`)) });
  const del = useMutation({
    mutationFn: () => api<{ deleted: boolean; chunks: number; evidence_removed: number }>("DELETE", path(`/sources/${id}`)),
    onSuccess: (r) => { toast(`Quelle gelöscht (${r.chunks} Fundstellen, ${r.evidence_removed} Belege entfernt).`, "ok"); void qc.invalidateQueries(); onClose(); },
  });
  if (!id) return <div className="card muted">Wählen Sie einen Eintrag, um die Originalquelle und ihre Fundstellen zu sehen.</div>;
  return (
    <div className="card">
      <Async q={q}>
        {(s) => (
          <>
            <div className="row"><h2 style={{ margin: 0 }}>{s.title}</h2><span className="spacer" /><button className="btn small" onClick={onClose}>{t.common.close}</button></div>
            <p className="small muted">{s.source_kind} · <Badge kind={s.origin === "synthetic" ? "warn" : undefined}>{t.origin[s.origin] ?? s.origin}</Badge> {s.customer_name ? <>· {s.customer_name}</> : null}</p>
            {s.file_id ? <p className="small"><a href={path(`/files/${s.file_id}/download`)}>Originaldatei herunterladen ({s.file_name})</a></p> : null}
            {s.chunks.map((c) => (
              <div key={c.id} className="quote" id={`chunk-${c.id}`}>
                <div className="small muted">Fundstelle: {locatorText(c.locator) || `Abschnitt ${c.ordinal + 1}`}</div>
                <div>{c.text}</div>
                {c.problem_ids.length ? <div className="small">{c.problem_ids.map((p) => <Link key={p} href={`/w/${tenantId}/problems/${p}`}>Problem öffnen </Link>)}</div> : null}
              </div>
            ))}
            {can("admin") ? (
              <div style={{ marginTop: ".75rem" }}>
                <button className="btn danger small" disabled={del.isPending} onClick={() => { if (window.confirm("Quelle, Fundstellen und daraus abgeleitete Belege löschen? Das lässt sich nicht rückgängig machen.")) del.mutate(); }}>Quelle löschen</button>
                {del.isError ? <ErrorNotice error={del.error} /> : null}
              </div>
            ) : null}
          </>
        )}
      </Async>
    </div>
  );
}
