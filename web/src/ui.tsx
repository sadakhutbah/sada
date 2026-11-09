import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-[10px] border border-line bg-white p-4.5 ${className}`}>{children}</section>
  );
}

export function Kicker({ children }: { children: ReactNode }) {
  return (
    <p className="m-0 mb-2 text-[0.75rem] font-semibold uppercase tracking-[0.14em] text-green">{children}</p>
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
}: { children: ReactNode; variante?: "plein" | "ghost" | "leaf" | "danger" | "light" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const styles = {
    plein: "border-deep bg-deep text-white hover:bg-deeper",
    ghost: "border-deep bg-transparent text-deep hover:bg-paper-2",
    leaf: "border-leaf bg-leaf text-night hover:bg-leaf-2",
    danger: "border-live bg-live text-white hover:bg-[#b42c23]",
    light: "border-white/25 bg-white/10 text-white hover:bg-white/18",
  }[variante];
  return (
    <button
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

/** Sélecteur segmenté : un seul bloc, pas une seconde rangée de pastilles. */
export function Segmented({
  items,
  valeur,
  onChange,
}: {
  items: { cle: string; label: string; eteint?: boolean }[];
  valeur: string;
  onChange: (cle: string) => void;
}) {
  return (
    <div className="mb-3 inline-flex flex-wrap gap-0.5 rounded-[9px] border border-line bg-paper-2 p-[3px]">
      {items.map((it) => (
        <button
          key={it.cle}
          onClick={() => onChange(it.cle)}
          className={`inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-[7px] border-0 px-3.5 py-1 text-[0.88rem] font-semibold ${
            valeur === it.cle
              ? "bg-white text-deeper shadow-[0_1px_2px_rgba(23,38,28,0.14),inset_0_-2px_0_var(--color-leaf)]"
              : "bg-transparent text-muted hover:bg-white/60 hover:text-ink"
          }`}
        >
          {it.label}
          {it.eteint && <span className="inline-block size-[7px] rounded-full border-[1.5px] border-muted" />}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, aide, children }: { label: string; aide?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[0.9rem] font-semibold">{label}</span>
      {children}
      {aide && <span className="mt-1 block text-[0.82rem] text-muted">{aide}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-[#bfcbb6] bg-white px-3 py-2.5 text-ink disabled:bg-paper-2 disabled:text-muted";
