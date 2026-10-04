import { t } from "./i18n";

export interface MoneyBlock { by_currency: Record<string, string>; unknown_count: number; counted: number }

const DEFAULT_TZ = "Europe/Berlin";

export function fmtDate(iso: string | null | undefined, tz = DEFAULT_TZ, withTime = false): string {
  if (!iso) return t.common.none;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return t.common.none;
  return new Intl.DateTimeFormat("de-DE", {
    timeZone: tz, day: "2-digit", month: "2-digit", year: "numeric", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(d);
}

export function fmtMoney(amount: string | number | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "" || !currency) return t.common.unknown;
  const n = typeof amount === "string" ? Number(amount) : amount;
  if (Number.isNaN(n)) return t.common.unknown;
  return new Intl.NumberFormat("de-DE", { style: "currency", currency, maximumFractionDigits: 0 }).format(n);
}

/** Money per currency, never summed across currencies; unknown items are named, not counted as zero. */
export function fmtMoneyBlock(block: MoneyBlock | undefined | null): string {
  if (!block) return t.common.unknown;
  const parts = Object.entries(block.by_currency).map(([cur, amt]) => fmtMoney(amt, cur));
  const base = parts.length ? parts.join(" · ") : "keine bekannten Beträge";
  return block.unknown_count ? `${base} (${block.unknown_count} unbekannt)` : base;
}

export function fmtPercent(x: string | number | null | undefined): string {
  if (x === null || x === undefined) return t.common.unknown;
  const n = Number(x);
  return Number.isNaN(n) ? t.common.unknown : `${(n * 100).toFixed(0)} %`;
}

export function fmtScore(x: string | null | undefined): string {
  if (x === null || x === undefined) return "nicht berechenbar";
  return Number(x).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function locatorText(loc: Record<string, unknown> | undefined): string {
  if (!loc) return "";
  const bits: string[] = [];
  if (loc.file) bits.push(String(loc.file));
  if (loc.page) bits.push(`Seite ${loc.page}`);
  if (loc.row) bits.push(`Zeile ${loc.row}`);
  if (loc.paragraph) bits.push(`Absatz ${loc.paragraph}`);
  return bits.join(", ");
}
