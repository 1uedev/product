"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtMoneyBlock, fmtScore, type MoneyBlock } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, ErrorNotice, Warnings } from "@/components/ui";
import { useWorkspace } from "@/components/session";

interface Result { name: string; total: string | null; weight_coverage: string; missing: string[]; blocked: boolean; contributions: { criterion: string; effective_weight: string; value: string | null; points: string; status: string; note: string }[] }
interface Compare {
  policy: { name: string; weights: Record<string, string>; missing_value_policy: string; weights_overridden?: boolean }; results: Result[]; ranking: string[];
  sensitivity: { stable: boolean | null; base_ranking: string[]; scenarios: { criterion: string; direction: string; ranking: string[]; ranking_changed: boolean }[]; score_range: Record<string, { min: string; max: string }>; note: string };
  initiatives: { name: string; unique_customers: number; statements: number; arr: MoneyBlock; pipeline_attributed: MoneyBlock; warnings: { code: string; message: string }[] }[];
  portfolio: { unique_customers: number; customer_problem_pairs: number; arr: MoneyBlock; annual_sales: MoneyBlock; open_pipeline_customer_level: MoneyBlock; customers_without_value: number };
}

function Inner() {
  const { path, tenantId } = useWorkspace();
  const params = useSearchParams();
  const list = useQuery({ queryKey: ["initiatives-all"], queryFn: () => api<Page<S["InitiativeOut"]>>("GET", path("/initiatives"), { query: { limit: 100 } }) });
  const policies = useQuery({ queryKey: ["policies"], queryFn: () => api<S["PolicyOut"][]>("GET", path("/scoring-policies")) });
  const [ids, setIds] = useState<Set<string>>(new Set((params.get("ids") ?? "").split(",").filter(Boolean)));
  const [policyId, setPolicyId] = useState("");
  const [weights, setWeights] = useState<Record<string, string> | null>(null);
  const active = policies.data?.find((p) => p.id === policyId) ?? policies.data?.find((p) => p.active);
  const effective = weights ?? (active ? Object.fromEntries(Object.entries(active.weights).map(([k, v]) => [k, String(v)])) : {});
  const sum = Object.values(effective).reduce((a, v) => a + (Number(v) || 0), 0);
  const compare = useMutation({
    mutationFn: () => api<Compare>("POST", path("/scoring/compare"), { body: { initiative_ids: [...ids], policy_id: active?.id ?? null, ...(weights ? { weights_override: weights } : {}) } }),
  });
  return (
    <>
      <div className="page-head"><div><h1>Vergleich</h1><p className="muted">Transparentes, deterministisches Scoring. Gewichte lassen sich ändern; die Sensitivität zeigt, ob sich die Rangfolge dadurch verschiebt. Ein Score ist eine Entscheidungshilfe, keine Prognose.</p></div></div>
      <div className="grid cols-2">
        <section className="card" aria-labelledby="sel"><h2 id="sel">Initiativen auswählen</h2>
          <Async q={list} isEmpty={(d) => d.items.length === 0} empty="Noch keine Initiativen.">{(d) => (
            <ul style={{ listStyle: "none", padding: 0 }}>{d.items.map((i) => <li key={i.id}><label className="check"><input type="checkbox" checked={ids.has(i.id)} onChange={() => setIds((s) => { const n = new Set(s); if (n.has(i.id)) n.delete(i.id); else n.add(i.id); return n; })} /> {i.title} <span className="small muted">({i.problem_title})</span></label></li>)}</ul>)}
          </Async>
        </section>
        <section className="card" aria-labelledby="wts"><h2 id="wts">Gewichte</h2>
          <Async q={policies}>{() => (
            <>
              <label htmlFor="pol">Policy</label>
              <select id="pol" value={active?.id ?? ""} onChange={(e) => { setPolicyId(e.target.value); setWeights(null); }}>{policies.data?.map((p) => <option key={p.id} value={p.id}>{p.name}{p.active ? " (aktiv)" : ""}</option>)}</select>
              <div className="grid cols-2" style={{ marginTop: ".5rem" }}>
                {Object.entries(t.criteria).map(([k, v]) => (
                  <div className="field" key={k}><label htmlFor={`w-${k}`}>{v}</label><input id={`w-${k}`} type="number" min="0" max="1" step="0.05" value={effective[k] ?? "0"} onChange={(e) => setWeights({ ...effective, [k]: e.target.value })} data-testid={`weight-${k}`} /></div>
                ))}
              </div>
              <p className={`small ${Math.abs(sum - 1) < 0.0001 ? "muted" : ""}`} role="status">Summe: {sum.toFixed(2)} {Math.abs(sum - 1) >= 0.0001 ? "(muss 1 ergeben)" : ""}</p>
              <div className="row"><button className="btn" onClick={() => setWeights(null)} disabled={!weights}>Zurücksetzen</button></div>
            </>
          )}</Async>
        </section>
      </div>
      <div className="row" style={{ margin: "1rem 0" }}><button className="btn primary" disabled={ids.size === 0 || compare.isPending || Math.abs(sum - 1) >= 0.0001} onClick={() => compare.mutate()} data-testid="compare-run">Vergleich berechnen</button></div>
      {compare.isError ? <ErrorNotice error={compare.error} /> : null}
      {compare.data ? <Results c={compare.data} tenantId={tenantId} /> : null}
    </>
  );
}

function Results({ c }: { c: Compare; tenantId: string }) {
  const criteria = Array.from(new Set(c.results.flatMap((r) => r.contributions.map((x) => x.criterion))));
  return (
    <div className="stack" data-testid="compare-results">
      <section className="card"><h2>Ergebnis</h2>
        <p className="small muted">Policy „{c.policy.name}“{c.policy.weights_overridden ? " mit geänderten Gewichten (nicht gespeichert)" : ""}, fehlende Werte: {c.policy.missing_value_policy}.</p>
        <div className="table-wrap"><table><thead><tr><th>Kriterium</th>{c.results.map((r) => <th key={r.name} className="num">{r.name}</th>)}</tr></thead><tbody>
          {criteria.map((k) => <tr key={k}><td>{t.criteria[k]}</td>{c.results.map((r) => { const x = r.contributions.find((y) => y.criterion === k); return <td key={r.name} className="num" title={x?.note}>{x ? (x.value ?? "unbekannt") : "–"}<div className="small muted">{x ? `${x.points} P.` : ""}</div></td>; })}</tr>)}
          <tr><th>Gesamt</th>{c.results.map((r) => <th key={r.name} className="num" data-testid="score-total">{fmtScore(r.total)}</th>)}</tr>
          <tr><td>Datenabdeckung der Gewichte</td>{c.results.map((r) => <td key={r.name} className="num">{Math.round(Number(r.weight_coverage) * 100)} %</td>)}</tr>
        </tbody></table></div>
        {c.results.some((r) => r.missing.length) ? <div className="notice warn small">Fehlende Werte werden nach der Policy behandelt und sind oben als „unbekannt“ markiert, nicht als 0.</div> : null}
      </section>
      <section className="card"><h2>Sensitivität bei ±25 % Gewichtsänderung</h2>
        <p role="status">{c.sensitivity.stable === null ? "Keine Rangfolge berechenbar." : c.sensitivity.stable ? <strong>Die Rangfolge ist stabil.</strong> : <strong>Die Rangfolge ist nicht stabil: einzelne Gewichte entscheiden über die Reihenfolge.</strong>} Basis: {c.sensitivity.base_ranking.join(" > ") || "–"}</p>
        <div className="table-wrap"><table><thead><tr><th>Kriterium</th><th>Richtung</th><th>Rangfolge</th><th>Änderung</th></tr></thead><tbody>{c.sensitivity.scenarios.map((s, i) => <tr key={i}><td>{t.criteria[s.criterion]}</td><td>{s.direction === "up" ? "+25 %" : "−25 %"}</td><td>{s.ranking.join(" > ")}</td><td>{s.ranking_changed ? <strong>ja</strong> : "nein"}</td></tr>)}</tbody></table></div>
        <p className="small muted">{c.sensitivity.note}</p>
      </section>
      <section className="card"><h2>Gesamtbetrachtung ohne Mehrfachzählung</h2>
        <p>{c.portfolio.unique_customers} eindeutige Kunden (statt {c.portfolio.customer_problem_pairs} Kunde-Problem-Paaren). Jeder Kunde geht mit seinem Umsatz nur einmal ein.</p>
        <dl className="kv"><dt>ARR</dt><dd>{fmtMoneyBlock(c.portfolio.arr)}</dd><dt>Jahresumsatz</dt><dd>{fmtMoneyBlock(c.portfolio.annual_sales)}</dd><dt>Offene Pipeline der Kunden</dt><dd>{fmtMoneyBlock(c.portfolio.open_pipeline_customer_level)}</dd><dt>Kunden ohne Wert</dt><dd>{c.portfolio.customers_without_value}</dd></dl>
      </section>
      {c.initiatives.map((i) => <Warnings key={i.name} items={i.warnings.map((w) => ({ ...w, message: `${i.name}: ${w.message}` }))} />)}
    </div>
  );
}
export default function ComparePage() { return <Suspense><Inner /></Suspense>; }
