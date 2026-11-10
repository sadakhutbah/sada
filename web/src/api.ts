/** JSON API client of the back-office. One rule: no session = back to sign-in. */
export type Session = {
  user: { id: number; name: string; role: string; role_label: string; permissions: string[] };
  mosque: { id: number; slug: string; name: string; timezone: string; logo: string; clip_title: string };
  csrf: string;
  counts: { pending: number };
};

/** Error carrying the HTTP status and the server message, to display it as-is. */
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

// Session anti-replay token: the server requires it on every write (`X-CSRF`).
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

/** Write: same client, with the session token in a header. */
function ecrire<T>(methode: string, chemin: string, corps?: unknown): Promise<T> {
  return demande<T>(chemin, {
    method: methode,
    headers: { "Content-Type": "application/json", "X-CSRF": jeton },
    body: corps === undefined ? undefined : JSON.stringify(corps),
  });
}

/** A schedule event as the server returns it. */
export type AgendaEvent = {
  id: string; kind: string; kind_label: string; title: string; speaker: string; series: string;
  session: number | null; max_min: number | null; publish: boolean;
  mode: "once" | "weekly"; date: string; time: string; weekday: string; after: string; offset_min: number;
  next: string; past: boolean;
};
export type EventsList = {
  items: AgendaEvent[]; total: number; kinds: Record<string, string>;
  weekdays: string[]; prayers: string[]; today: string;
};
/** Event form: flat fields, as text (the server reads them back as-is). */
export type EventForm = Record<
  "kind" | "title" | "speaker" | "series" | "session" | "max_min" | "publish" | "mode" | "date" | "time" | "weekday" | "after" | "offset_min",
  string
>;

export const api = {
  session: () => demande<Session>("/session"),
  logout: () => ecrire<{ ok: boolean }>("POST", "/logout"),
  dashboard: () => demande<any>("/dashboard"),
  /** Search, filter and paging are done by the server: the list grows over the years. */
  sermons: (opts: { q?: string; mois?: string; pending?: boolean; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    if (opts.mois) p.set("mois", opts.mois);
    if (opts.pending) p.set("pending", "1");
    if (opts.limit) p.set("limit", String(opts.limit));
    if (opts.offset) p.set("offset", String(opts.offset));
    const qs = p.toString();
    return demande<{ items: any[]; total: number; count: number; offset: number; pending: number }>(
      `/sermons${qs ? `?${qs}` : ""}`);
  },
  /** The months that contain khutbas: the period picker is based on them. */
  sermonsMois: () => demande<{ items: { mois: string; n: number }[] }>("/sermons/mois"),
  sermon: (id: number) => demande<any>(`/sermons/${id}`),
  clips: (status = "") =>
    demande<{ items: any[]; tabs: string[]; counts: Record<string, number> }>(`/clips${status ? `?status=${status}` : ""}`),
  clip: (id: number) => demande<any>(`/clips/${id}`),
  publications: () => demande<any>("/publications"),
  settings: () => demande<any>("/settings"),
  users: () => demande<any>("/users"),
  audit: (page = 0, taille = 25, action = "") =>
    demande<{ items: any[]; total: number; actions: string[] }>(
      `/audit?limit=${taille}&offset=${page * taille}${action ? `&action=${encodeURIComponent(action)}` : ""}`),
  /** Dars and conference schedule (file read by the orchestrator). */
  events: () => demande<EventsList>("/events"),
  folders: (path = "") => demande<any>(`/folders${path ? `?path=${encodeURIComponent(path)}` : ""}`),

  // Writes
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
  creerEvenement: (corps: EventForm) => ecrire<{ ok: boolean; message: string; id: string }>("POST", "/events", corps),
  majEvenement: (id: string, corps: EventForm) =>
    ecrire<{ ok: boolean; message: string; id: string }>("PUT", `/events/${encodeURIComponent(id)}`, corps),
  supprimerEvenement: (id: string) =>
    ecrire<{ ok: boolean; message: string }>("DELETE", `/events/${encodeURIComponent(id)}`),
  /** Logo: multipart upload (the browser sets the Content-Type header itself). */
  envoyerLogo: (fichier: File) => {
    const corps = new FormData();
    corps.append("logo", fichier);
    return demande<{ ok: boolean; message: string; logo: string }>("/settings/logo", {
      method: "POST",
      headers: { "X-CSRF": jeton },
      body: corps,
    });
  },
  retirerLogo: () => ecrire<{ ok: boolean; message: string }>("DELETE", "/settings/logo"),
  /** Connector credentials: a secret left empty or masked keeps its stored value server-side. */
  saveConnector: (key: string, enabled: boolean, values: Record<string, string>) =>
    ecrire<any>("POST", `/settings/connectors/${key}`, { ...values, enabled: enabled ? "1" : "0" }),
  saveNotifications: (body: Record<string, string>) => ecrire<any>("PUT", "/settings/notifications", body),
  destinations: (targets: Array<{ id?: number; label: string; url: string; enabled: boolean }>) =>
    ecrire<any>("PUT", "/settings/live", { targets }),
  testNotifications: () => ecrire<any>("POST", "/settings/notifications/test"),
};
