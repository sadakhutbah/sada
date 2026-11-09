/** Client de l'API JSON du back-office. Une seule règle : pas de session = retour à la connexion. */
export type Session = {
  user: { id: number; name: string; role: string; role_label: string; permissions: string[] };
  mosque: { id: number; slug: string; name: string; timezone: string; logo: string; clip_title: string };
  csrf: string;
};

let surExpiration: (() => void) | null = null;
export function onUnauthorized(fn: () => void) {
  surExpiration = fn;
}

async function demande<T>(chemin: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`/api/v1${chemin}`, { credentials: "same-origin", ...init });
  if (r.status === 401) {
    surExpiration?.();
    throw new Error("unauthorized");
  }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export const api = {
  session: () => demande<Session>("/session"),
  dashboard: () => demande<any>("/dashboard"),
  sermons: () => demande<{ items: any[] }>("/sermons"),
  sermon: (id: number) => demande<any>(`/sermons/${id}`),
  clips: (status = "") => demande<{ items: any[]; status_labels: Record<string, string> }>(`/clips${status ? `?status=${status}` : ""}`),
  publications: () => demande<any>("/publications"),
  settings: () => demande<any>("/settings"),
  users: () => demande<any>("/users"),
  audit: () => demande<{ items: any[] }>("/audit"),
};
