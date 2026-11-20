/** Prayer-time form values: plain data and conversions, shared by Settings and the wizard. */

export const PRIERES = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"] as const;
export const ADHANS = ["fajr", "dhuhr", "asr", "maghrib", "isha"] as const;
export const METHODES = ["uoif", "mwl", "isna", "egypt", "makkah", "karachi", "morocco", "diyanet", "custom"] as const;

export type Calc = {
  lat: string; lon: string; method: string; fajr_angle: string; isha_angle: string; isha_delay_min: string;
  asr: string; high_lats: string;
  offsets: Record<string, string>; bounds: Record<string, [string, string]>; iqama: Record<string, string>;
};

const texte = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

/** The calculation as the form edits it (everything as text), from what the server holds. */
export function calcDepuis(c: any): Calc {
  const dict = (o: any) => Object.fromEntries(Object.entries<any>(o ?? {}).map(([k, v]) => [k, texte(v)]));
  return {
    lat: texte(c?.lat), lon: texte(c?.lon), method: texte(c?.method) || "uoif",
    fajr_angle: texte(c?.fajr_angle), isha_angle: texte(c?.isha_angle), isha_delay_min: texte(c?.isha_delay_min),
    asr: texte(c?.asr) || "standard", high_lats: texte(c?.high_lats) || "nightmiddle",
    offsets: dict(c?.offsets), iqama: dict(c?.iqama),
    bounds: Object.fromEntries(Object.entries<any>(c?.bounds ?? {}).map(([k, v]) => [k, [texte(v?.[0]), texte(v?.[1])]])),
  };
}

/** What is sent: empty boxes are left out, a limit pair is kept only when one side is filled. */
export function calcVersServeur(c: Calc): Record<string, unknown> {
  const plein = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v.trim() !== ""));
  return {
    ...c,
    offsets: plein(c.offsets), iqama: plein(c.iqama),
    bounds: Object.fromEntries(Object.entries(c.bounds).filter(([, v]) => v[0].trim() !== "" || v[1].trim() !== "")),
  };
}

export const moisCourant = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};

