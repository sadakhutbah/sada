/** JSON API client of the back-office. One rule: no session = back to sign-in. */
import i18n from "./i18n";
export type Session = {
  user: { id: number; name: string; role: string; role_label: string; permissions: string[];
          must_change_password?: boolean };
  mosque: { id: number; slug: string; name: string; timezone: string; logo: string; clip_title: string;
            needs_onboarding?: boolean };
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
  const headers = new Headers(init.headers);
  headers.set("X-Sada-Lang", i18n.language || "fr");
  const r = await fetch(`/api/v1${chemin}`, { credentials: "same-origin", ...init, headers });
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

/** One processing run after the live: transcription, then the clips. The server sends stable
 *  keys (`etat`) and free data (label, dates, error), never interface text. */
export type Traitement = {
  run: string; session: number; label: string; date: string;
  etat: "attente" | "en_cours" | "termine" | "echec";
  debut: string | null; fin: string | null; erreur: string | null;
};
export type TraitementsList = { items: Traitement[]; actif: boolean };

/** Metadata of an off-air recording imported from a file (the file itself is the body). */
export type VideoImport = {
  filename: string; kind: string; title: string; speaker: string; series: string;
  session_no: string; date: string;
};

/** Branding options of a video: what is shown, the texts, timings and the logo's place. */
export type HabillageOptions = {
  intro_on: boolean; title: string; subtitle: string; intro_s: number;
  lower_on: boolean; speaker: string; role: string; lower_at: number; lower_s: number;
  logo_on: boolean; logo_pos: "tl" | "tr" | "bl" | "br"; logo_size: "s" | "m" | "l"; logo_opacity: number;
  bar_on: boolean;
  outro_on: boolean; outro_text: string; outro_link: string; outro_s: number;
};

export type Habillage = {
  options: HabillageOptions;
  status: "" | "en_cours" | "pret" | "echec";
  progress: number; error: string; started_at: string; finished_at: string; duration_s: number | null;
  video_ready: boolean; video_v: number; size_mb: number | null; preview_ready: boolean; preview_v: number;
  has_source: boolean; has_logo: boolean; can_edit: boolean; mosque: string;
  colors: { green: string; gold: string };
};

export const api = {
  session: () => demande<Session>("/session"),
  logout: () => ecrire<{ ok: boolean }>("POST", "/logout"),
  dashboard: () => demande<any>("/dashboard"),
  /** Runs after the live, newest first (ten at most). Absent backend: the call answers 404 and the
   *  panel stays hidden. */
  traitements: () => demande<TraitementsList>("/traitements"),
  relancerTraitement: (run: string, session: number) =>
    ecrire<{ ok: boolean; message: string }>("POST", "/traitements/relancer", { run, session }),
  /** Search, filter and paging are done by the server: the list grows over the years. */
  sermons: (opts: { q?: string; mois?: string; pending?: boolean; limit?: number; offset?: number; kind?: string } = {}) => {
    const p = new URLSearchParams();
    if (opts.kind) p.set("kind", opts.kind);
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
  sermonsMois: (kind = "") => demande<{ items: { mois: string; n: number }[] }>(`/sermons/mois${kind ? `?kind=${kind}` : ""}`),
  sermon: (id: number) => demande<any>(`/sermons/${id}`),
  clips: (status = "", limit = 20, offset = 0) => {
    const p = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) p.set("status", status);
    return demande<{ items: any[]; tabs: string[]; counts: Record<string, number>; total: number }>(`/clips?${p}`);
  },
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
  /** Clip chosen by hand on the khutba video (bounds in session time, seconds). */
  creerClip: (kid: number, start: number, end: number, kind: string, title: string) =>
    ecrire<{ id: number; message: string }>("POST", `/sermons/${kid}/clips`,
      { start: start.toFixed(2), end: end.toFixed(2), kind, title }),
  bornesClip: (cid: number, start: number, end: number, title: string) =>
    ecrire<any>("POST", `/clips/${cid}/bounds`, { start: start.toFixed(2), end: end.toFixed(2), title }),
  publier: (cid: number, connector: string, caption = "") =>
    ecrire<any>("POST", `/clips/${cid}/publish`, { connector, caption }),
  correction: (kid: number, seg: number, field: string, value: string) =>
    ecrire<any>("POST", `/sermons/${kid}/revisions`, { seg: String(seg), field, value }),
  ajouterSousTitre: (kid: number, start: number, end: number, text: string) =>
    ecrire<any>("POST", `/sermons/${kid}/subtitles`, { start: String(start), end: String(end), text }),
  retirerSousTitre: (kid: number, sid: number) => ecrire<any>("POST", `/sermons/${kid}/subtitles/${sid}/delete`),
  habillage: (kid: number) => demande<Habillage>(`/sermons/${kid}/habillage`),
  habillageApercu: (kid: number, options: HabillageOptions) =>
    ecrire<Habillage & { message: string }>("POST", `/sermons/${kid}/habillage/apercu`, { options }),
  habillageLancer: (kid: number, options: HabillageOptions) =>
    ecrire<Habillage & { message: string }>("POST", `/sermons/${kid}/habillage`, { options }),
  intervenant: (kid: number, imam_id: string, speaker: string) =>
    ecrire<any>("POST", `/sermons/${kid}/speaker`, { imam_id, speaker }),
  motDePasse: (current: string, nouveau: string, confirm: string) =>
    ecrire<any>("POST", "/account/password", { current, new: nouveau, confirm }),
  creerCompte: (corps: Record<string, string>) => ecrire<any>("POST", "/users", corps),
  majCompte: (id: number, corps: Record<string, string>) => ecrire<any>("PATCH", `/users/${id}`, corps),
  mdpCompte: (id: number, password: string) => ecrire<any>("POST", `/users/${id}/password`, { password }),
  reglagesMosquee: (corps: Record<string, string>) => ecrire<any>("PATCH", "/settings/mosque", corps),
  adresseInstance: (public_url: string) => ecrire<any>("PUT", "/settings/instance", { public_url }),
  modele: (corps: Record<string, string>) => ecrire<any>("PUT", "/settings/model", corps),
  traitement: (clips: string, enabled: boolean) =>
    ecrire<any>("PUT", "/settings/processing", { clips, enabled: enabled ? "1" : "0" }),
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
  /** Recorded video (off-air camera): the raw file is the body, never multipart, so a file of
   *  several GB is streamed. XHR is used for the upload progress; `xhr.abort()` cancels it.
   *  The server answers the usual `{ok, message}` shape, errors included. */
  importerVideo: (fichier: File, meta: VideoImport, progression: (pct: number) => void) => {
    const p = new URLSearchParams({
      filename: fichier.name, kind: meta.kind, title: meta.title,
      speaker: meta.speaker, series: meta.series, date: meta.date,
    });
    if (meta.session_no) p.set("session_no", meta.session_no);
    const xhr = new XMLHttpRequest();
    const promesse = new Promise<{ ok: boolean; message: string; run: string }>((resolve, reject) => {
      xhr.open("POST", `/api/v1/imports/video?${p.toString()}`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-CSRF", jeton);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) progression(Math.min(100, Math.round((e.loaded / e.total) * 100)));
      };
      xhr.onload = () => {
        let json: any = null;
        try { json = JSON.parse(xhr.responseText); } catch { /* not JSON: the status speaks */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          progression(100);
          resolve(json);
          return;
        }
        if (xhr.status === 401) surExpiration?.();
        reject(new ApiError(xhr.status, (json && (json.message || json.error)) || `${xhr.status} ${xhr.statusText}`));
      };
      xhr.onerror = () => reject(new ApiError(0, ""));
      xhr.onabort = () => reject(new ApiError(0, ""));
      xhr.send(fichier);
    });
    return { xhr, promesse };
  },
  /** Connector credentials: a secret left empty or masked keeps its stored value server-side. */
  saveConnector: (key: string, enabled: boolean, values: Record<string, string>) =>
    ecrire<any>("POST", `/settings/connectors/${key}`, { ...values, enabled: enabled ? "1" : "0" }),
  saveNotifications: (body: Record<string, string>) => ecrire<any>("PUT", "/settings/notifications", body),
  destinations: (targets: Array<{ id?: number; label: string; url: string; enabled: boolean }>) =>
    ecrire<any>("PUT", "/settings/live", { targets }),
  /** Video input: a camera address, or "a computer pushes its picture" (the relay's address is built server-side). */
  saveInput: (mode: string, url: string) => ecrire<any>("PUT", "/settings/input", { mode, url }),
  /** Replays bucket (S3 / R2): the secret comes back masked, sending the mask keeps it. */
  stockage: (v: Record<string, any>) => ecrire<any>("PUT", "/settings/storage", v),
  testStockage: (v: Record<string, any>) =>
    ecrire<{ ok: boolean; detail: string }>("POST", "/settings/storage/test", v),
  /** Texts of the live screens (slates): {écran: {champ: texte}}, an empty field means "default". */
  ecrans: (screens: Record<string, Record<string, string>>) => ecrire<any>("PUT", "/settings/screens", { screens }),
  /** Common template texts: the mosque's Arabic lines, never a text invented by the application. */
  variablesEcran: (variables: Record<string, string>) =>
    ecrire<any>("PUT", "/settings/screens/variables", { variables }),
  /** Multipart import of an `ecrans.html` template: the server checks it, keeps it, applies its texts. */
  importerGabarit: (fichier: File) => {
    const corps = new FormData();
    corps.append("file", fichier);
    return demande<any>("/settings/screens/import", {
      method: "POST",
      headers: { "X-CSRF": jeton },
      body: corps,
    });
  },
  /** PNG of one screen, drawn by the engine with the texts currently typed (not saved yet). */
  apercuEcran: async (screen: string, screens: Record<string, Record<string, string>>) => {
    const r = await fetch("/api/v1/settings/screens/preview", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF": jeton },
      body: JSON.stringify({ screen, screens }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => null);
      throw new ApiError(r.status, (j && (j.message || j.error)) || `${r.status} ${r.statusText}`);
    }
    return URL.createObjectURL(await r.blob());
  },
  /** The wizard is offered once: completing or skipping it records the choice. */
  onboardingDone: (done = true) => ecrire<any>("PUT", "/settings/onboarding", { done }),
  testInput: (mode: string, url: string) =>
    ecrire<{ ok: boolean; detail: string; url: string }>("POST", "/settings/input/test", { mode, url }),
  testNotifications: () => ecrire<any>("POST", "/settings/notifications/test"),
};
