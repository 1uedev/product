"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Badge, Field, Pager, StatusBadge, Tabs } from "@/components/ui";
import { useWorkspace } from "@/components/session";

function Inner() {
  const { path, ws, tenantId } = useWorkspace();
  const params = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") ?? "customers");
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [stage, setStage] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(params.get("id"));
  const customers = useQuery({ queryKey: ["customers", applied, offset], enabled: tab === "customers", queryFn: () => api<Page<S["CustomerOut"]>>("GET", path("/customers"), { query: { q: applied, limit: 20, offset } }) });
  const opps = useQuery({ queryKey: ["opps", applied, stage, offset], enabled: tab === "opps", queryFn: () => api<Page<S["OpportunityOut"]>>("GET", path("/opportunities"), { query: { q: applied, stage, limit: 20, offset } }) });
  const detail = useQuery({ queryKey: ["customer", selected], enabled: !!selected, queryFn: () => api<S["CustomerDetail"]>("GET", path(`/customers/${selected}`)) });
  return (
    <>
      <div className="page-head"><div><h1>Kunden & Opportunities</h1><p className="muted">Umsatzwerte werden mit Basis (ARR oder Jahresumsatz) und Währung gezeigt. Fehlende Werte sind „unbekannt“, nicht 0.</p></div></div>
      <Tabs tabs={[{ id: "customers", label: "Kunden" }, { id: "opps", label: "Opportunities" }]} value={tab} onChange={(x) => { setTab(x); setOffset(0); }} />
      <form className="row" role="search" onSubmit={(e) => { e.preventDefault(); setApplied(q); setOffset(0); }} style={{ marginBottom: ".75rem" }}>
        <div style={{ minWidth: 240 }}><Field id="cq" label="Suche"><input id="cq" type="search" value={q} onChange={(e) => setQ(e.target.value)} /></Field></div>
        {tab === "opps" ? <div><Field id="cst" label="Phase"><select id="cst" value={stage} onChange={(e) => { setStage(e.target.value); setOffset(0); }}><option value="">alle</option>{Object.entries(t.stage).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field></div> : null}
        <button className="btn" type="submit" style={{ marginTop: ".4rem" }}>{t.common.search}</button>
      </form>
      <div className="split">
        <div>
          {tab === "customers" ? (
            <Async q={customers} isEmpty={(d) => d.items.length === 0} empty="Keine Kunden gefunden. Importieren Sie Kundenstammdaten.">
              {(d) => <div className="card"><div className="table-wrap"><table data-testid="customer-table"><thead><tr><th>ID</th><th>Name</th><th>Segment</th><th>Land</th><th className="num">Wert</th><th>Basis</th><th className="num">Feedback</th><th className="num">Opps</th></tr></thead><tbody>
                {d.items.map((c) => <tr key={c.id} aria-selected={selected === c.id}><td>{c.external_id}</td><td><button className="btn link" onClick={() => setSelected(c.id)}>{c.name}</button></td><td>{c.segment ?? "–"}</td><td>{c.country ?? "–"}</td>
                  <td className="num money">{fmtMoney(c.commercial_value, c.currency)}</td><td>{t.valueBasis[c.value_basis]}{c.value_as_of ? <span className="small muted"> (Stand {String(c.value_as_of)})</span> : null}</td><td className="num">{c.feedback_count}</td><td className="num">{c.opportunity_count}</td></tr>)}
              </tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>}
            </Async>
          ) : (
            <Async q={opps} isEmpty={(d) => d.items.length === 0} empty="Keine Opportunities gefunden.">
              {(d) => <div className="card"><div className="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Kunde</th><th>Phase</th><th className="num">Betrag</th><th>Abschluss</th></tr></thead><tbody>
                {d.items.map((o) => <tr key={o.id}><td>{o.external_id}</td><td>{o.name}</td><td><button className="btn link" onClick={() => { setSelected(o.customer_account_id); }}>{o.customer_name}</button></td><td><Badge>{t.stage[o.stage]}</Badge></td><td className="num money">{fmtMoney(o.amount, o.currency)}</td><td>{o.closed_at ? String(o.closed_at) : "–"}</td></tr>)}
              </tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>}
            </Async>
          )}
        </div>
        <aside className="panel" aria-label="Kundendetail">
          {!selected ? <div className="card muted">Wählen Sie einen Kunden für Details.</div> : (
            <div className="card"><Async q={detail}>{(c) => (
              <>
                <div className="row"><h2 style={{ margin: 0 }}>{c.name}</h2><span className="spacer" /><button className="btn small" onClick={() => setSelected(null)}>{t.common.close}</button></div>
                <dl className="kv"><dt>ID</dt><dd>{c.external_id}</dd><dt>Segment</dt><dd>{c.segment ?? "–"}</dd><dt>Wert</dt><dd>{fmtMoney(c.commercial_value, c.currency)} ({t.valueBasis[c.value_basis]})</dd></dl>
                <h3>Opportunities</h3>{c.opportunities.length ? <ul>{c.opportunities.map((o) => <li key={o.id}>{o.name}: {t.stage[o.stage]}, {fmtMoney(o.amount, o.currency)}</li>)}</ul> : <p className="muted">Keine.</p>}
                <h3>Probleme (unterstützende Aussagen)</h3>{c.problems.length ? <ul>{c.problems.map((p) => <li key={String(p.id)}><Link href={`/w/${tenantId}/problems/${p.id}`}>{String(p.title)}</Link> ({String(p.statements)} Aussagen) <StatusBadge value={String(p.status)} map={t.status.problem} /></li>)}</ul> : <p className="muted">Keine.</p>}
                <h3>Letzte Aussagen</h3>{c.feedback.slice(0, 5).map((f) => <div className="quote" key={String(f.id)}><div className="small muted">{fmtDate(String(f.occurred_at), ws.timezone)} · {t.channel[String(f.channel)]}</div>{String(f.body)}</div>)}
              </>
            )}</Async></div>
          )}
        </aside>
      </div>
    </>
  );
}
export default function CustomersPage() { return <Suspense><Inner /></Suspense>; }
