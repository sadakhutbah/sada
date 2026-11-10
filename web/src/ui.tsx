import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-[10px] border border-line bg-white p-4.5 ${className}`}>{children}</section>
  );
}

export function Kicker({ children, sombre = false }: { children: ReactNode; sombre?: boolean }) {
  return (
    <p className={`m-0 mb-2 text-[0.75rem] font-semibold uppercase leading-none tracking-[0.12em] ${sombre ? "text-leaf" : "text-green"}`}>
      {children}
    </p>
  );
}

export function Title({ children, grand = false }: { children: ReactNode; grand?: boolean }) {
  return (
    <h2 className={`m-0 mb-2 font-serif font-normal leading-[1.15] text-deeper ${grand ? "text-[2.1rem]" : "text-[1.55rem]"}`}>
      {children}
    </h2>
  );
}

export function Btn({
  children,
  variante = "plein",
  className = "",
  ...reste
}: { children: ReactNode; variante?: "plein" | "ghost" | "leaf" | "danger" | "dangerGhost" | "light" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const styles = {
    plein: "border-deep bg-deep text-white hover:bg-deeper",
    ghost: "border-deep bg-transparent text-deep hover:bg-paper-2",
    leaf: "border-leaf bg-leaf text-night hover:bg-leaf-2",
    danger: "border-live bg-live text-white hover:bg-[#b42c23]",
    // Secondary destructive action: visible without stealing the main action's place.
    dangerGhost: "border-live/50 bg-transparent text-[#b42c23] hover:bg-err-bg",
    light: "border-white/25 bg-white/10 text-white hover:bg-white/18",
  }[variante];
  return (
    <button
      type="button"          /* default inside a form; callers pass type="submit" where needed */
      className={`inline-flex min-h-11 cursor-pointer items-center justify-center gap-1.5 rounded-[9px] border px-4 py-2.5 font-semibold ${styles} disabled:cursor-not-allowed disabled:opacity-45 ${className}`}
      {...reste}
    >
      {children}
    </button>
  );
}

const PILLS: Record<string, string> = {
  a_valider: "bg-warn-bg text-warn",
  valide: "bg-paper-2 text-deep",
  rejete: "bg-err-bg text-[#8a2219]",
  publie: "bg-deep text-white",
};

export function Pill({ statut, label }: { statut: string; label: string }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-[0.8rem] font-bold ${PILLS[statut] ?? "bg-[#eef0ea] text-muted"}`}>
      {label}
    </span>
  );
}

/** Top-level tabs: separate pills, the active one filled in deep green (.tabs in the back-office). */
export function Tabs({
  items,
  valeur,
  onChange,
}: {
  items: { cle: string; label: string; compte?: number; eteint?: boolean }[];
  valeur: string;
  onChange: (cle: string) => void;
}) {
  return (
    <div className="mb-3.5 flex flex-wrap gap-2">
      {items.map((it) => (
        <button
          key={it.cle}
          type="button"          /* a tab never submits the form around it */
          onClick={() => onChange(it.cle)}
          disabled={it.eteint}
          className={`inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[0.93rem] font-semibold ${
            it.eteint ? "cursor-not-allowed border-line bg-white/60 text-muted/60" : "cursor-pointer"
          } ${
            valeur === it.cle
              ? "border-deep bg-deep text-white"
              : "border-line bg-white text-deep hover:border-leaf hover:bg-paper-2"
          }`}
        >
          {it.label}
          {it.compte !== undefined && (
            <span className={`rounded-full px-1.5 text-[0.78rem] ${valeur === it.cle ? "bg-white/20" : "bg-paper-2 text-muted"}`}>
              {it.compte}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

/** Segmented selector: a single block, for the sub-tabs of one screen.
 *  `compte` (optional) shows a small total next to the label. */
export function Segmented({
  items,
  valeur,
  onChange,
}: {
  items: { cle: string; label: string; compte?: number; eteint?: boolean }[];
  valeur: string;
  onChange: (cle: string) => void;
}) {
  return (
    <div className="mb-3 inline-flex flex-wrap gap-0.5 rounded-[9px] border border-line bg-paper-2 p-[3px]">
      {items.map((it) => (
        <button
          key={it.cle}
          type="button"          /* a tab never submits the form around it */
          onClick={() => onChange(it.cle)}
          disabled={it.eteint}
          className={`inline-flex min-h-8 items-center gap-1.5 rounded-[7px] border-0 px-3.5 py-1 text-[0.88rem] font-semibold ${
            it.eteint ? "cursor-not-allowed text-muted/60" : "cursor-pointer"
          } ${
            valeur === it.cle
              ? "bg-white text-deeper shadow-[0_1px_2px_rgba(23,38,28,0.14),inset_0_-2px_0_var(--color-leaf)]"
              : "bg-transparent text-muted hover:bg-white/60 hover:text-ink"
          }`}
        >
          {it.label}
          {it.compte !== undefined && (
            <span className={`rounded-full px-1.5 text-[0.76rem] tabular-nums ${valeur === it.cle ? "bg-paper-2 text-deep" : "bg-white/70 text-muted"}`}>
              {it.compte}
            </span>
          )}
          {it.eteint && <span className="inline-block size-[7px] rounded-full border-[1.5px] border-muted" />}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, aide, children, className = "" }:
  { label: string; aide?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-[0.9rem] font-semibold">{label}</span>
      {children}
      {aide && <span className="mt-1 block text-[0.82rem] text-muted">{aide}</span>}
    </label>
  );
}

/** Table headers and cells: a single horizontal padding (8 px) so titles and values line up,
 *  and no border under the last row (it would double the card's). */
export const thClass =
  "border-b border-line px-2 py-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted";
export const tdClass = "border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0";

/** Simple table, no sortable header: the last row keeps no border. */
export function Table({ entetes, children }: { entetes: string[]; children: ReactNode }) {
  return (
    <table className="w-full border-collapse text-[0.95rem]">
      <thead>
        <tr>
          {entetes.map((h) => (
            <th key={h} className={thClass}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

export const inputClass =
  "w-full rounded-lg border border-[#bfcbb6] bg-white px-3 py-2.5 text-ink read-only:cursor-default read-only:bg-paper-2 read-only:text-muted disabled:bg-paper-2 disabled:text-muted";
