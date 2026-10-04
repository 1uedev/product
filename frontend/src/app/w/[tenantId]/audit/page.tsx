"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Async, Field, Pager } from "@/components/ui";
import { useWorkspace } from "@/components/session";

export default function AuditPage() {
  const { path, ws, can } = useWorkspace();
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ["audit", applied, offset], enabled: can("admin"), queryFn: () => api<Page<S["AuditOut"]>>("GET", path("/audit"), { query: { action: applied, limit: 30, offset } }) });
  if (!can("admin")) return <><h1>Audit</h1><div className="notice warn">Das Audit-Protokoll ist für Admins und Owner sichtbar.</div></>;
  return (
    <>
      <div className="page-head"><div><h1>Audit-Protokoll</h1><p className="muted">Mitgliedschaften, Änderungen, Freigaben und Exporte. Das Protokoll ist nicht änderbar und enthält keine Dokumentinhalte.</p></div></div>
      <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(action); setOffset(0); }}><div style={{ minWidth: 260 }}><Field id="aa" label="Aktion beginnt mit"><input id="aa" value={action} onChange={(e) => setAction(e.target.value)} placeholder="z. B. decision, export, member" /></Field></div><button className="btn" type="submit" style={{ marginTop: ".4rem" }}>Filtern</button></form>
      <Async q={q} isEmpty={(d) => d.items.length === 0} empty="Keine Ereignisse.">
        {(d) => (
          <div className="card"><div className="table-wrap"><table data-testid="audit-table"><thead><tr><th>Zeitpunkt</th><th>Person</th><th>Aktion</th><th>Objekt</th><th>Details</th></tr></thead><tbody>
            {d.items.map((e) => <tr key={e.id}><td>{fmtDate(e.occurred_at, ws.timezone, true)}</td><td>{e.actor_name ?? "System"}</td><td><code>{e.action}</code></td><td>{e.entity_type}</td><td className="small">{Object.entries(e.metadata).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}</td></tr>)}
          </tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>
        )}
      </Async>
    </>
  );
}
