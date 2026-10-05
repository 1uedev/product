"use client";

import { fmtMoneyBlock, fmtPercent, type MoneyBlock } from "@/lib/format";
import { Warnings } from "@/components/ui";

export interface Metrics {
  supporting_statements: number; supporting_customers: number; unassigned_statements: number; contradicting_statements: number; contradicting_customers: number;
  conflicted_customers: number; context_statements: number; top_customer_share: string | null;
  segments: { segment: string; affected_customers: number; total_customers: number; share: string }[];
  arr: MoneyBlock; annual_sales: MoneyBlock; pipeline_attributed: MoneyBlock; lost_attributed: MoneyBlock; pipeline_customer_level: MoneyBlock; lost_customer_level: MoneyBlock;
  customers_without_value: number;
  age: { oldest_days: number | null; newest_days: number | null; median_days: number | null; older_than_fresh_count: number; fresh_days: number };
  coverage: { affected_customers: number; total_customers: number; affected_share: string | null; value_known_customers: number; value_known_share: string | null; customers_with_any_feedback: number | null };
  verified_share: string | null; fresh_share: string | null; warnings: { code: string; message: string }[]; fresh_days: number;
}

/** Frequency (unique customers AND statements), economic context (separated by kind and currency), evidence age and coverage. */
export function MetricsPanel({ m, compact }: { m: Metrics; compact?: boolean }) {
  return (
    <div className="stack">
      <div className="grid cols-4">
        <div className="card kpi" data-testid="metric-customers"><span className="value">{m.supporting_customers}</span><span className="label">eindeutige Kunden</span><span className="small muted">{m.supporting_statements} Aussagen insgesamt{m.unassigned_statements ? `, davon ${m.unassigned_statements} ohne Kundenzuordnung` : ""}</span></div>
        <div className="card kpi"><span className="value">{m.contradicting_statements}</span><span className="label">widersprechende Aussagen</span><span className="small muted">von {m.contradicting_customers} Kunden{m.conflicted_customers ? `, ${m.conflicted_customers} Kunden mit beiden Signalen` : ""}</span></div>
        <div className="card kpi"><span className="value">{fmtPercent(m.coverage.affected_share)}</span><span className="label">Anteil betroffener Kunden</span><span className="small muted">{m.coverage.affected_customers} von {m.coverage.total_customers} Kunden</span></div>
        <div className="card kpi"><span className="value">{m.age.median_days ?? "–"}</span><span className="label">Median-Alter der Belege (Tage)</span><span className="small muted">ältester {m.age.oldest_days ?? "–"} Tage, {m.age.older_than_fresh_count} älter als {m.fresh_days} Tage</span></div>
      </div>
      <Warnings items={m.warnings} />
      <section className="card" aria-label="Wirtschaftlicher Kontext">
        <h3>Wirtschaftlicher Kontext (nicht addiert, nicht umgerechnet)</h3>
        <dl className="kv">
          <dt>Zugeordnetes ARR</dt><dd className="money" data-testid="metric-arr">{fmtMoneyBlock(m.arr)}</dd>
          <dt>Zugeordneter Jahresumsatz</dt><dd className="money">{fmtMoneyBlock(m.annual_sales)}</dd>
          <dt>Offene Pipeline (genannt)</dt><dd className="money">{fmtMoneyBlock(m.pipeline_attributed)}</dd>
          <dt>Verlorenes Volumen (genannt)</dt><dd className="money">{fmtMoneyBlock(m.lost_attributed)}</dd>
          {!compact ? <><dt>Weitere offene Pipeline der Kunden</dt><dd className="money muted">{fmtMoneyBlock(m.pipeline_customer_level)} <span className="small">(Kontext, nicht zugeordnet)</span></dd></> : null}
          <dt>Kunden ohne bekannten Wert</dt><dd>{m.customers_without_value}</dd>
        </dl>
        <p className="small muted">ARR, Jahresumsatz, Pipeline und verlorenes Volumen sind verschiedene Größen und werden nie zu einer Summe oder einem „sicheren Mehrumsatz“ zusammengezogen.</p>
      </section>
      {!compact ? (
        <section className="card" aria-label="Segmente">
          <h3>Betroffene Segmente</h3>
          <div className="table-wrap"><table><thead><tr><th>Segment</th><th className="num">Betroffen</th><th className="num">Kunden gesamt</th><th className="num">Anteil</th></tr></thead><tbody>
            {m.segments.map((s) => <tr key={s.segment}><td>{s.segment}</td><td className="num">{s.affected_customers}</td><td className="num">{s.total_customers}</td><td className="num">{fmtPercent(s.share)}</td></tr>)}
          </tbody></table></div>
        </section>
      ) : null}
    </div>
  );
}
