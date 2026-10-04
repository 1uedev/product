"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Field, Pager, StatusBadge } from "@/components/ui";
import { useWorkspace } from "@/components/session";

export default function DecisionsPage() {
  const { path, ws, tenantId } = useWorkspace();
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ["decisions", state, offset], queryFn: () => api<Page<S["DecisionListItem"]>>("GET", path("/decisions"), { query: { state, limit: 25, offset } }) });
  return (
    <>
      <div className="page-head"><div><h1>Entscheidungsvorlagen</h1><p className="muted">Eine Freigabe erzeugt eine unveränderliche Revision. Neue Erkenntnisse führen zu einer neuen Revision, die alte bleibt als historische Entscheidung erhalten.</p></div></div>
      <div style={{ maxWidth: 260 }}><Field id="ds" label="Status"><select id="ds" value={state} onChange={(e) => { setState(e.target.value); setOffset(0); }}><option value="">alle</option>{Object.entries(t.status.decision).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field></div>
      <Async q={q} isEmpty={(d) => d.items.length === 0} empty={<p>Noch keine Entscheidungsvorlagen. Legen Sie sie auf der Seite einer Initiative an.</p>}>
        {(d) => (
          <div className="card"><div className="table-wrap"><table data-testid="decision-table"><thead><tr><th>Vorlage</th><th>Initiative</th><th className="num">Revision</th><th>Status</th><th>Freigegeben</th><th>Geändert</th></tr></thead><tbody>
            {d.items.map((x) => <tr key={x.id}><td><Link href={`/w/${tenantId}/decisions/${x.id}`}>{x.title || "Entscheidungsvorlage"}</Link></td><td><Link href={`/w/${tenantId}/initiatives/${x.initiative_id}`}>{x.initiative_title}</Link></td><td className="num">{x.revision}</td><td><StatusBadge value={x.state} map={t.status.decision} /></td><td>{fmtDate(x.approved_at, ws.timezone)}</td><td>{fmtDate(x.updated_at, ws.timezone)}</td></tr>)}
          </tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>
        )}
      </Async>
    </>
  );
}
