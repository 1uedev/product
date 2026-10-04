"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useWorkspace } from "@/components/session";
import { Badge, DemoBadge } from "@/components/ui";

export function Shell({ children }: { children: ReactNode }) {
  const { ws, me, can, tenantId } = useWorkspace();
  const pathname = usePathname();
  const router = useRouter();
  const base = `/w/${tenantId}`;
  const items: { href: string; label: string; show: boolean }[] = [
    { href: base, label: t.nav.overview, show: true },
    { href: `${base}/import`, label: t.nav.import, show: true },
    { href: `${base}/sources`, label: t.nav.sources, show: true },
    { href: `${base}/customers`, label: t.nav.customers, show: true },
    { href: `${base}/problems`, label: t.nav.problems, show: true },
    { href: `${base}/initiatives`, label: t.nav.initiatives, show: true },
    { href: `${base}/compare`, label: t.nav.compare, show: true },
    { href: `${base}/decisions`, label: t.nav.decisions, show: true },
    { href: `${base}/jobs`, label: t.nav.jobs, show: true },
    { href: `${base}/members`, label: t.nav.members, show: true },
    { href: `${base}/audit`, label: t.nav.audit, show: can("admin") },
    { href: `${base}/settings`, label: t.nav.settings, show: can("admin") },
  ];
  const current = (href: string) => (href === base ? pathname === base : pathname.startsWith(href));
  async function signOut() {
    try {
      const out = await api<{ logout_url: string | null }>("POST", "/api/v1/auth/logout");
      // the logout URL points at the identity provider (other path on the same origin or another host): a full navigation is intended
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = out.logout_url ?? "/";
    } catch { window.location.href = "/"; }
  }
  return (
    <div className="shell">
      <a className="skip-link" href="#main">Zum Inhalt springen</a>
      {me.demo_mode ? <div className="banner" role="note">Demo-Modus: synthetische Daten und bekannte Demo-Zugänge. Nicht für echte Kundendaten verwenden.</div> : null}
      <header className="topbar">
        <Link href="/" className="brand">{t.appName}</Link>
        <label className="sr-only" htmlFor="ws-switch">{t.common.workspace}</label>
        <select id="ws-switch" style={{ width: "auto", maxWidth: "18rem" }} value={tenantId} onChange={(e) => router.push(`/w/${e.target.value}`)}>
          {me.workspaces.map((w) => <option key={w.tenant_id} value={w.tenant_id}>{w.name}</option>)}
        </select>
        <Badge kind="info">{t.roles[ws.role] ?? ws.role}</Badge>
        {ws.ai.demo ? <DemoBadge /> : <Badge>{`KI: ${ws.ai.model}`}</Badge>}
        <span className="spacer" />
        <span className="small muted" data-testid="user-name">{me.display_name ?? me.email}</span>
        <button className="btn small" onClick={() => void signOut()}>{t.common.signOut}</button>
      </header>
      <nav className="nav" aria-label="Hauptnavigation">
        {items.filter((i) => i.show).map((i) => <Link key={i.href} href={i.href} aria-current={current(i.href) ? "page" : undefined}>{i.label}</Link>)}
      </nav>
      <main id="main" tabIndex={-1}>{children}</main>
    </div>
  );
}
