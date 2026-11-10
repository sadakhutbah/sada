/** Shared formatting: long-form dates and durations, as the template back-office did. */

export function dateFr(valeur?: string | null, langue = "fr"): string {
  if (!valeur) return "—";
  const d = new Date(`${valeur}T12:00:00`);
  if (Number.isNaN(d.getTime())) return valeur;
  return new Intl.DateTimeFormat(langue, { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(d);
}

/** Seconds -> mm:ss (clips last one to three minutes). */
export function mmss(secondes?: number | null): string {
  const s = Math.max(0, Math.round(Number(secondes) || 0));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Live clock: null when out of service, otherwise the orchestrator's time. */
export function horloge(valeur?: string | null): string {
  return valeur || "--:--:--";
}
