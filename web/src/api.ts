/** Client de l'API JSON du back-office. Une seule règle : pas de session = retour à la connexion. */
export type Session = {
  user: { id: number; name: string; role: string; role_label: string; permissions: string[] };
  mosque: { id: number; slug: string; name: string; timezone: string; logo: string; clip_title: string };
  csrf: string;
  counts: { pending: number };
};

/** Erreur portant le code HTTP et le message du serveur, pour l'afficher tel quel. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let surExpiration: (() => void) | null = null;
export function onUnauthorized(fn: () => void) {
  surExpiration = fn;
}

// Jeton anti-rejeu de la session : le serveur l'exige sur toute écriture (`X-CSRF`).
let jeton = "";
export function setCsrf(valeur: string) {
  jeton = valeur;
}

async function demande<T>(chemin: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`/api/v1${chemin}`, { credentials: "same-origin", ...init });
  if (r.status === 401) {
    surExpiration?.();
    throw new ApiError(401, "connexion requise");
  }
  const json = (r.headers.get("content-type") || "").includes("json") ? await r.json().catch(() => null) : null;
  if (!r.ok) {
    const message = (json && (json.message || json.error)) || `${r.status} ${r.statusText}`;
    throw new ApiError(r.status, message);
  }
  return json as T;
}

/** Écriture : même client, avec le jeton de session en en-tête. */
function ecrire<T>(methode: string, chemin: string, corps?: unknown): Promise<T> {
  return demande<T>(chemin, {
    method: methode,
    headers: { "Content-Type": "application/json", "X-CSRF": jeton },
    body: corps === undefined ? undefined : JSON.stringify(corps),
  });
}

export const api = {
  session: () => demande<Session>("/session"),
  logout: () => ecrire<{ ok: boolean }>("POST", "/logout"),
  dashboard: () => demande<any>("/dashboard"),
  sermons: () => demande<{ items: any[] }>("/sermons"),
  sermon: (id: number) => demande<any>(`/sermons/${id}`),
  clips: (status = "") =>
    demande<{ items: any[]; status_labels: Record<string, string>; counts: Record<string, number> }>(`/clips${status ? `?status=${status}` : ""}`),
  clip: (id: number) => demande<any>(`/clips/${id}`),
  publications: () => demande<any>("/publications"),
  settings: () => demande<any>("/settings"),
  users: () => demande<any>("/users"),
  audit: () => demande<{ items: any[] }>("/audit"),
  folders: (path = "") => demande<any>(`/folders${path ? `?path=${encodeURIComponent(path)}` : ""}`),

  // Écritures
  liveCommande: (cmd: string, corps: Record<string, string> = {}) => ecrire<any>("POST", `/live/${cmd}`, corps),
  importer: (run = "") => ecrire<any>("POST", "/import", { run }),
  decision: (cid: number, decision: string, reason = "") => ecrire<any>("POST", `/clips/${cid}/decision`, { decision, reason }),
  regenerer: (cid: number) => ecrire<any>("POST", `/clips/${cid}/regenerate`),
  publier: (cid: number, connector: string, caption = "") =>
    ecrire<any>("POST", `/clips/${cid}/publish`, { connector, caption }),
  correction: (kid: number, seg: number, field: string, value: string) =>
    ecrire<any>("POST", `/sermons/${kid}/revisions`, { seg: String(seg), field, value }),
  intervenant: (kid: number, imam_id: string, speaker: string) =>
    ecrire<any>("POST", `/sermons/${kid}/speaker`, { imam_id, speaker }),
  motDePasse: (current: string, nouveau: string, confirm: string) =>
    ecrire<any>("POST", "/account/password", { current, new: nouveau, confirm }),
  creerCompte: (corps: Record<string, string>) => ecrire<any>("POST", "/users", corps),
  majCompte: (id: number, corps: Record<string, string>) => ecrire<any>("PATCH", `/users/${id}`, corps),
  reglagesMosquee: (corps: Record<string, string>) => ecrire<any>("PATCH", "/settings/mosque", corps),
  modele: (corps: Record<string, string>) => ecrire<any>("PUT", "/settings/model", corps),
  testerModele: () => ecrire<any>("POST", "/settings/model/test"),
};
