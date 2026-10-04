import type { components } from "@/api/schema";

export type S = components["schemas"];

export class ApiError extends Error {
  status: number;
  code: string;
  detail?: string;
  requestId?: string;
  extra: Record<string, unknown>;
  constructor(status: number, body: Record<string, unknown> | null, fallback: string) {
    super((body?.title as string) || fallback);
    this.status = status;
    this.code = (body?.code as string) || `http_${status}`;
    this.detail = body?.detail as string | undefined;
    this.requestId = body?.request_id as string | undefined;
    this.extra = body ?? {};
  }
}

let csrfToken = "";
export function setCsrfToken(token: string): void { csrfToken = token; }

export function loginUrl(next?: string): string {
  const target = next ?? (typeof window !== "undefined" ? window.location.pathname + window.location.search : "/");
  return `/api/v1/auth/login?next=${encodeURIComponent(target)}`;
}

type Query = Record<string, string | number | boolean | undefined | null>;
interface Opts { query?: Query; body?: unknown; version?: number | string; form?: FormData }

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const qs = sp.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function api<T>(method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE", path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  if (opts.version !== undefined) headers["If-Match"] = `"${opts.version}"`;
  const res = await fetch(withQuery(path, opts.query), { method, headers, body, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let json: Record<string, unknown> | null = null;
  try { json = text ? (JSON.parse(text) as Record<string, unknown>) : null; } catch { json = null; }
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && !path.includes("/auth/")) window.location.assign(loginUrl());
    throw new ApiError(res.status, json, res.statusText);
  }
  return json as T;
}

export const ws = (tenantId: string, path = ""): string => `/api/v1/workspaces/${tenantId}${path}`;

/** Version for If-Match from a loaded entity. */
export function versionOf(x: { version: number }): number { return x.version; }

export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
