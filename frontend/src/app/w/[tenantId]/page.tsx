"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { fmtMoneyBlock, fmtDate, type MoneyBlock } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Kpi, StatusBadge } from "@/components/ui";
import { useWorkspace } from "@/components/session";

interface Dashboard {
  counts: { customers: number; opportunities: number; feedback_items: number; sources: number; problems: Record<string, number>; initiatives: Record<string, number>; decisions: Record<string, number> };
  coverage: { customers_with_feedback: number; customers_total: number; unassigned_feedback: number; feedback_older_than_fresh_days: number; fresh_days: number };
  money: { arr: MoneyBlock; annual_sales: MoneyBlock; open_pipeline: MoneyBlock; lost_volume: MoneyBlock; customers_without_value: number; note: string };
  segments: Record<string, number>;
  top_problems: { id: string; title: string; customers: number; statements: number; contradictions: number }[];
  recent_jobs: { id: string; kind: string; status: string; created_at: string; error_code: string | null }[];
  decisions_in_review: { id: string; title: string; revision: number }[];
  source_origins: Record<string, number>;
}

export default function Overview() {
  const { path, tenantId, ws } = useWorkspace();
  const q = useQuery({ queryKey: ["dashboard", tenantId], queryFn: () => api<Dashboard>("GET", path("/dashboard")) });
  const base = `/w/${tenantId}`;
  return (
    <>
      <div className="page-head"><div><h1>Übersicht</h1><p className="muted">{ws.name}: alle Zahlen stammen aus den gespeicherten Daten dieses Workspaces.</p></div></div>
      <Async q={q} isEmpty={(d) => d.counts.customers === 0 && d.counts.feedback_items === 0}
        empty={<><p>Dieser Workspace ist noch leer.</p><Link className="btn primary" href={`${base}/import`}>Daten importieren</Link></>}>
        {(d) => (
          <div className="stack">
            <div className="grid cols-4">
              <Kpi label="Kunden" value={d.counts.customers} />
              <Kpi label="Opportunities" value={d.counts.opportunities} />
              <Kpi label="Feedbackeinträge" value={d.counts.feedback_items} hint={`${d.coverage.unassigned_feedback} noch keinem Problem zugeordnet`} />
              <Kpi label="Kunden mit Feedback" value={`${d.coverage.customers_with_feedback} / ${d.coverage.customers_total}`} hint="Abdeckungsgrad der Datenbasis" />
            </div>
            <div className="grid cols-4">
              <Kpi label="Probleme bestätigt" value={d.counts.problems.confirmed ?? 0} hint={`${d.counts.problems.proposed ?? 0} vorgeschlagen`} />
              <Kpi label="Initiativen" value={Object.entries(d.counts.initiatives).reduce((a, [, n]) => a + n, 0)} />
              <Kpi label="Entscheidungen in Prüfung" value={d.counts.decisions.in_review ?? 0} />
              <Kpi label="Freigegeben" value={d.counts.decisions.approved ?? 0} />
            </div>
            <section className="card" aria-labelledby="money">
              <h2 id="money">Wirtschaftlicher Kontext (je Art und Währung getrennt)</h2>
              <p className="small muted">{d.money.note}</p>
              <dl className="kv">
                <dt>ARR bekannter Kunden</dt><dd className="money" data-testid="kpi-arr">{fmtMoneyBlock(d.money.arr)}</dd>
                <dt>Jahresumsatz (nicht ARR)</dt><dd className="money">{fmtMoneyBlock(d.money.annual_sales)}</dd>
                <dt>Offene Pipeline</dt><dd className="money">{fmtMoneyBlock(d.money.open_pipeline)}</dd>
                <dt>Verlorenes Auftragsvolumen</dt><dd className="money">{fmtMoneyBlock(d.money.lost_volume)}</dd>
                <dt>Kunden ohne bekannten Wert</dt><dd>{d.money.customers_without_value}</dd>
              </dl>
            </section>
            <div className="grid cols-2">
              <section className="card" aria-labelledby="top">
                <h2 id="top">Häufigste Probleme (nach eindeutigen Kunden)</h2>
                {d.top_problems.length === 0 ? <p className="muted">Noch keine Probleme. <Link href={`${base}/problems`}>Analyse starten</Link></p> : (
                  <div className="table-wrap"><table><thead><tr><th>Problem</th><th className="num">Kunden</th><th className="num">Aussagen</th><th className="num">Widerspr.</th></tr></thead>
                    <tbody>{d.top_problems.map((p) => (
                      <tr key={p.id}><td><Link href={`${base}/problems/${p.id}`}>{p.title}</Link></td><td className="num">{p.customers}</td><td className="num">{p.statements}</td><td className="num">{p.contradictions}</td></tr>
                    ))}</tbody></table></div>
                )}
              </section>
              <section className="card" aria-labelledby="recent">
                <h2 id="recent">Letzte Aufgaben</h2>
                {d.recent_jobs.length === 0 ? <p className="muted">Keine Aufgaben.</p> : (
                  <ul>{d.recent_jobs.map((j) => <li key={j.id}>{t.jobKind[j.kind] ?? j.kind} · <StatusBadge value={j.status} map={t.status.job} /> · <span className="small muted">{fmtDate(j.created_at, ws.timezone, true)}</span></li>)}</ul>
                )}
                {d.decisions_in_review.length ? <><h3>Wartet auf Freigabe</h3><ul>{d.decisions_in_review.map((x) => <li key={x.id}><Link href={`${base}/decisions/${x.id}`}>{x.title || "Entscheidungsvorlage"} (Revision {x.revision})</Link></li>)}</ul></> : null}
              </section>
            </div>
            <div className="notice">Datenbasis: {d.coverage.feedback_older_than_fresh_days} von {d.counts.feedback_items} Feedbackeinträgen sind älter als {d.coverage.fresh_days} Tage. Herkunft der Quellen: {Object.entries(d.source_origins).map(([k, v]) => `${t.origin[k] ?? k}: ${v}`).join(", ") || "–"}.</div>
          </div>
        )}
      </Async>
    </>
  );
}
