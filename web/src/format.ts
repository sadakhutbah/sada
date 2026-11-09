/** Mises en forme partagées : dates en toutes lettres et durées, comme le back-office en gabarits. */

export function dateFr(valeur?: string | null, langue = "fr"): string {
  if (!valeur) return "—";
  const d = new Date(`${valeur}T12:00:00`);
  if (Number.isNaN(d.getTime())) return valeur;
  return new Intl.DateTimeFormat(langue, { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(d);
}

/** Secondes -> mm:ss (les clips durent une à trois minutes). */
export function mmss(secondes?: number | null): string {
  const s = Math.max(0, Math.round(Number(secondes) || 0));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Horloge du live : null hors service, sinon l'heure de l'orchestrateur. */
export function horloge(valeur?: string | null): string {
  return valeur || "--:--:--";
}
