"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { ApiError, api, loginUrl, setCsrfToken, ws, type S } from "@/lib/api";
import { Async } from "@/components/ui";

export type Me = S["MeOut"];
export type Workspace = Omit<S["WorkspaceDetail"], "ai"> & { ai: { provider: string; model: string; demo: boolean } };
const ROLE_RANK: Record<string, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => { const me = await api<Me>("GET", "/api/v1/auth/me"); setCsrfToken(me.csrf_token); return me; },
    staleTime: 60_000,
  });
}

interface WorkspaceCtx { tenantId: string; ws: Workspace; me: Me; can: (minRole: "viewer" | "editor" | "admin" | "owner") => boolean; path: (p?: string) => string }
const Ctx = createContext<WorkspaceCtx | null>(null);
export function useWorkspace(): WorkspaceCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useWorkspace outside of workspace layout");
  return c;
}

export function WorkspaceProvider({ tenantId, children }: { tenantId: string; children: ReactNode }) {
  const me = useMe();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => { if (unauthenticated) window.location.assign(loginUrl()); }, [unauthenticated]);
  const wsq = useQuery({ queryKey: ["workspace", tenantId], queryFn: () => api<Workspace>("GET", ws(tenantId)), enabled: !!me.data });
  return (
    <Async q={me}>
      {(meData) => (
        <Async q={wsq}>
          {(w) => (
            <Ctx.Provider value={{ tenantId, ws: w, me: meData, can: (min) => ROLE_RANK[w.role]! >= ROLE_RANK[min]!, path: (p = "") => ws(tenantId, p) }}>
              {children}
            </Ctx.Provider>
          )}
        </Async>
      )}
    </Async>
  );
}
