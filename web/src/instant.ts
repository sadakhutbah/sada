/** Instants in a khutba, as typed and shown in the clip forms. */

/** "1:05.4", "65.4" or "65,4" -> seconds; NaN when unreadable. */
export function lireInstant(v: string): number {
  const m = v.trim().replace(",", ".").match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  return m ? Number(m[1] ?? 0) * 60 + Number(m[2]) : NaN;
}

export const ecrireInstant = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
