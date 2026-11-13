import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api, type AgendaEvent, type EventForm, type EventsList } from "../api";
import { currentSession } from "../session";
import { Btn, Field, Pill, Segmented, inputClass } from "../ui";
import { idOnglet, idPanneau } from "../onglets";
import { ListeSeances } from "./Sermons";

/** Dars and conference agenda: the schedule the orchestrator reads, as an editable list.
 *  A single surface for the list; a single form, split into tabs (Event / Time),
 *  replacing the add button while entering. The data is the server's: the page
 *  computes nothing but the display of days and times. */

const VIDE: EventForm = {
  kind: "dars", title: "", speaker: "", series: "", session: "", max_min: "", publish: "1",
  mode: "weekly", date: "", time: "", weekday: "mardi", after: "isha", offset_min: "15",
};

/** ISO date taken at noon, so that no timezone makes the day slip. */
const jour = (iso: string) => new Date(`${iso}T12:00:00`);
const majuscule = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Name of a weekday in the interface language (the server speaks French). */
function nomJour(liste: string[], nom: string, langue: string): string {
  const i = liste.indexOf(nom);
  if (i < 0) return nom;
  return new Intl.DateTimeFormat(langue, { weekday: "long" }).format(new Date(2024, 0, 1 + i, 12));
}

function versFormulaire(e: AgendaEvent): EventForm {
  return {
    kind: e.kind, title: e.title, speaker: e.speaker, series: e.series,
    session: e.session == null ? "" : String(e.session), max_min: e.max_min == null ? "" : String(e.max_min),
    publish: e.publish ? "1" : "0", mode: e.mode, date: e.date, time: e.time,
    weekday: e.weekday || "mardi", after: e.after, offset_min: String(e.offset_min || 0),
  };
}

export default function Dars() {
  const { t, i18n } = useTranslation();
  const peutEcrire = currentSession()?.user.permissions.includes("assign") ?? false;
  const [liste, setListe] = useState<EventsList | null>(null);
  const [erreur, setErreur] = useState(false);
  const [filtre, setFiltre] = useState("tous");
  const [edition, setEdition] = useState<{ id: string | null; form: EventForm } | null>(null);
  const [aSupprimer, setASupprimer] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; texte: string } | null>(null);
  // Ce qu'on vient chercher d'abord : les dars enregistrés. Puis le programme, puis l'import.
  const [onglet, setOnglet] = useState("enregistres");

  const charger = () =>
    api.events().then((r) => { setListe(r); setErreur(false); }).catch(() => setErreur(true));
  useEffect(() => { charger(); }, []);

  const items = (liste?.items ?? []).filter((e) => filtre === "tous" || e.kind === filtre);
  const nombre = (k: string) => (liste?.items ?? []).filter((e) => k === "tous" || e.kind === k).length;

  const supprimer = async (id: string) => {
    try {
      await api.supprimerEvenement(id);
      setMessage({ ok: true, texte: t("dars.deleted") });
      setASupprimer(null);
      await charger();
    } catch (e: any) {
      setMessage({ ok: false, texte: e?.message || t("dars.saveError") });
    }
  };

  /** “every Tuesday, 15 min after Isha” / “21 Nov. 2026 at 18:30”, in the interface language. */
  const quand = (e: AgendaEvent): string => {
    const langue = i18n.language;
    const priere = e.after ? t(`dars.prayers.${e.after}`) : "";
    const heure = e.after
      ? Number(e.offset_min) ? t("dars.afterOffset", { min: e.offset_min, priere }) : t("dars.atPrayer", { priere })
      : t("dars.atTime", { heure: e.time });
    if (e.mode === "weekly") {
      return `${t("dars.weekly_", { jour: nomJour(liste?.weekdays ?? [], e.weekday, langue) })}, ${heure}`;
    }
    const date = new Intl.DateTimeFormat(langue, { weekday: "short", day: "numeric", month: "short", year: "numeric" })
      .format(jour(e.date)).replace(".", "");
    return t("dars.onceAt", { date, heure: e.time });
  };

  return (
    <div className="grid gap-5">
      <header className="grid gap-1">
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("dars.title")}</h1>
        <p className="m-0 min-h-[1.45rem] text-[0.95rem] text-muted">
          {liste ? t("dars.summary", { count: liste.total }) : (
            <span aria-hidden className="block h-[0.75rem] w-40 animate-pulse rounded-full bg-paper-2/70" />
          )}
        </p>
      </header>

      <div className="max-w-full min-w-0 [&>div]:mb-0">
        <Segmented
          valeur={onglet}
          onChange={(c) => { setOnglet(c); setMessage(null); }}
          items={[
            { cle: "enregistres", label: t("dars.tabs.recorded") },
            { cle: "programme", label: t("dars.tabs.programme") },
            ...(peutEcrire ? [{ cle: "import", label: t("dars.tabs.import") }] : []),
          ]}
        />
      </div>

      {onglet === "enregistres" && <ListeSeances genre="dars,conference" textes="dars.recorded" titre={false} />}

      {/* Import stays mounted while the schedule is shown: switching tabs never interrupts
          an upload in progress. */}
      {peutEcrire && (
        <div className={onglet === "import" ? "" : "hidden"}>
          <ImportVideo />
        </div>
      )}

      {onglet === "programme" && (
      <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="max-w-full min-w-0 [&>div]:mb-0">
          <Segmented
            valeur={filtre}
            onChange={setFiltre}
            items={[
              { cle: "tous", label: t("dars.filterAll"), compte: nombre("tous") },
              { cle: "dars", label: t("dars.filterDars"), compte: nombre("dars") },
              { cle: "conference", label: t("dars.filterConference"), compte: nombre("conference") },
            ]}
          />
        </div>
        {peutEcrire && !edition && (
          <Btn onClick={() => { setMessage(null); setEdition({ id: null, form: VIDE }); }}>{t("dars.add")}</Btn>
        )}
      </div>

      {message && (
        <p role="status" className={`m-0 rounded-lg px-3.5 py-2.5 text-[0.92rem] ${message.ok ? "bg-paper-2 text-deep" : "bg-err-bg text-[#7a1f20]"}`}>
          {message.texte}
        </p>
      )}

      {edition && liste && (
        <Formulaire
          liste={liste}
          depart={edition.form}
          id={edition.id}
          jourDe={(n) => nomJour(liste.weekdays, n, i18n.language)}
          onAnnuler={() => setEdition(null)}
          onEnregistre={async (texte) => {
            setEdition(null);
            setMessage({ ok: true, texte });
            await charger();
          }}
        />
      )}

      {erreur ? (
        <p className="rounded-[10px] border border-dashed border-line bg-white/60 px-6 py-12 text-center text-muted">{t("dars.loadError")}</p>
      ) : !liste ? (
        <div className="divide-y divide-line overflow-hidden rounded-[10px] border border-line bg-white" aria-busy="true" aria-label={t("common.loading")}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="grid gap-2 px-5 py-4">
              <div className="h-3 w-[40%] animate-pulse rounded bg-paper-2" />
              <div className="h-2.5 w-[60%] animate-pulse rounded bg-paper-2/70" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-[10px] border border-dashed border-line bg-white/60 px-6 py-14 text-center">
          <p className="mx-auto max-w-[54ch] text-[1.02rem] leading-relaxed text-muted">
            {liste.total === 0 ? t("dars.empty") : t("dars.emptyFilter")}
          </p>
        </div>
      ) : (
        <ul className="m-0 list-none divide-y divide-line overflow-hidden rounded-[10px] border border-line bg-white p-0">
          {items.map((e) => (
            <li key={e.id} className={`grid gap-x-4 gap-y-2 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:px-5 ${e.past ? "opacity-60" : ""}`}>
              <div className="min-w-0">
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                  <span className="font-semibold leading-snug text-deep">{e.title}</span>
                  <span className="text-[0.85rem] text-muted">· {e.speaker || t("dars.noSpeaker")}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[0.9rem] text-ink/85">
                  <span>{majuscule(quand(e))}</span>
                  {e.series && (
                    <span className="text-muted">
                      · {e.series}{e.session != null ? ` · ${t("dars.session_", { n: e.session })}` : ""}
                    </span>
                  )}
                  {e.mode === "weekly" && e.next && (
                    <span className="text-muted">
                      · {t("dars.next", { date: new Intl.DateTimeFormat(i18n.language, { day: "numeric", month: "short" }).format(jour(e.next)).replace(".", "") })}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                <Pill statut={e.kind === "dars" ? "valide" : "publie"} label={e.kind_label} />
                {!e.publish && <Pill statut="a_valider" label={t("dars.unlisted")} />}
                {e.past && <Pill statut="" label={t("dars.passed")} />}
                {peutEcrire && !edition && (
                  aSupprimer === e.id ? (
                    <>
                      <Btn variante="danger" className="min-h-9 px-3 py-1.5 text-[0.88rem]" onClick={() => supprimer(e.id)}>{t("dars.confirmDelete")}</Btn>
                      <Btn variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.88rem]" onClick={() => setASupprimer(null)}>{t("dars.cancel")}</Btn>
                    </>
                  ) : (
                    <>
                      <Btn variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.88rem]"
                        onClick={() => { setMessage(null); setEdition({ id: e.id, form: versFormulaire(e) }); }}>{t("dars.edit")}</Btn>
                      <Btn variante="dangerGhost" className="min-h-9 px-3 py-1.5 text-[0.88rem]" onClick={() => setASupprimer(e.id)}>{t("dars.delete")}</Btn>
                    </>
                  )
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="m-0 text-[0.85rem] text-muted">{peutEcrire ? t("dars.applyNote") : t("dars.readonly")}</p>
      </>
      )}
    </div>
  );
}

/** Choices of the import form; labels live in `dars.kinds`. */
const GENRES = ["dars", "conference", "khutba"] as const;

/** Local ISO date (no UTC shift): today is the default recording date. */
const aujourdhui = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

/** Import of a recorded video (camera off air): a single form. The raw file is sent as-is;
 *  the server's existing processing takes over and its tracking appears on the dashboard. */
function ImportVideo() {
  const { t, i18n } = useTranslation();
  const [fichier, setFichier] = useState<File | null>(null);
  const [kind, setKind] = useState("dars");
  const [titre, setTitre] = useState("");
  const [intervenant, setIntervenant] = useState("");
  const [serie, setSerie] = useState("");
  const [seance, setSeance] = useState("");
  const [date, setDate] = useState(aujourdhui);
  const [envoi, setEnvoi] = useState(false);
  const [pct, setPct] = useState(0);
  const [erreur, setErreur] = useState("");
  const [recu, setRecu] = useState(false);
  const choix = useRef<HTMLInputElement>(null);
  const requete = useRef<XMLHttpRequest | null>(null);
  const annule = useRef(false);

  // Closing the page during an upload would silently cut the file: the browser asks first.
  useEffect(() => {
    if (!envoi) return;
    const garde = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", garde);
    return () => window.removeEventListener("beforeunload", garde);
  }, [envoi]);

  /** Readable size, in the interface language (Mo / MB, Go / GB). */
  const taille = (o: number) => {
    const unite = o >= 1e9 ? "gigabyte" : o >= 1e6 ? "megabyte" : "kilobyte";
    const diviseur = o >= 1e9 ? 1e9 : o >= 1e6 ? 1e6 : 1e3;
    return new Intl.NumberFormat(i18n.language, { style: "unit", unit: unite, unitDisplay: "short", maximumFractionDigits: 1 })
      .format(o / diviseur);
  };

  const choisir = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFichier(e.target.files?.[0] ?? null);
    setErreur("");
    setRecu(false);
  };

  const envoyer = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!fichier) { setErreur(t("dars.importNeedFile")); return; }
    setEnvoi(true); setPct(0); setErreur(""); setRecu(false);
    annule.current = false;
    const { xhr, promesse } = api.importerVideo(fichier, {
      filename: fichier.name, kind, title: titre.trim(), speaker: intervenant.trim(),
      series: serie.trim(), session_no: seance, date,
    }, setPct);
    requete.current = xhr;
    try {
      await promesse;
      setRecu(true);
      setFichier(null);
      setTitre(""); setIntervenant(""); setSerie(""); setSeance("");
      if (choix.current) choix.current.value = "";
    } catch (e: any) {
      setErreur(annule.current ? t("dars.importCancelled") : e?.message || t("dars.importError"));
    } finally {
      setEnvoi(false);
      requete.current = null;
    }
  };

  return (
    <form onSubmit={envoyer} className="grid gap-4 rounded-[10px] border border-line bg-white p-4.5">
      <div className="grid gap-1">
        <h2 className="m-0 font-serif text-[1.35rem] font-normal text-deeper">{t("dars.importTitle")}</h2>
        <p className="m-0 text-[0.9rem] text-muted">{t("dars.importHint")}</p>
      </div>

      {recu && (
        <p role="status" className="m-0 rounded-lg bg-paper-2 px-3.5 py-2.5 text-[0.92rem] text-deep">
          {t("dars.importDone")}{" "}
          <Link to="/" className="font-semibold text-green underline">{t("dars.importGoDashboard")}</Link>
        </p>
      )}

      <div className="grid gap-3.5 sm:grid-cols-2">
        <Field label={t("dars.importFile")} className="sm:col-span-2">
          <div className="flex flex-wrap items-center gap-3">
            <input ref={choix} type="file" accept="video/*,.mkv,.mts" className="hidden" onChange={choisir} disabled={envoi} />
            <Btn variante="ghost" onClick={() => choix.current?.click()} disabled={envoi}>
              {fichier ? t("dars.importChange") : t("dars.importChoose")}
            </Btn>
            {fichier ? (
              <span className="min-w-0 break-all text-[0.9rem] text-ink">
                {fichier.name} <span className="text-muted">· {taille(fichier.size)}</span>
              </span>
            ) : (
              <span className="text-[0.9rem] text-muted">{t("dars.importNoFile")}</span>
            )}
          </div>
        </Field>
        <Field label={t("dars.kind")}>
          <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)} disabled={envoi}>
            {GENRES.map((k) => <option key={k} value={k}>{t(`dars.kinds.${k}`)}</option>)}
          </select>
        </Field>
        <Field label={t("dars.title_")}>
          <input className={inputClass} value={titre} onChange={(e) => setTitre(e.target.value)} required maxLength={200} disabled={envoi} />
        </Field>
        <Field label={t("dars.speaker")}>
          <input className={inputClass} value={intervenant} onChange={(e) => setIntervenant(e.target.value)} maxLength={120} disabled={envoi} />
        </Field>
        <Field label={t("dars.series")}>
          <input className={inputClass} value={serie} onChange={(e) => setSerie(e.target.value)} maxLength={200} disabled={envoi} />
        </Field>
        <Field label={t("dars.session")}>
          <input className={inputClass} type="number" min={1} step={1} value={seance} onChange={(e) => setSeance(e.target.value)} disabled={envoi} />
        </Field>
        <Field label={t("dars.importDate")}>
          <input className={inputClass} type="date" value={date} onChange={(e) => setDate(e.target.value)} required disabled={envoi} />
        </Field>
      </div>

      {envoi && (
        <div className="grid gap-2">
          <div className="h-2 overflow-hidden rounded-full bg-paper-2" role="progressbar"
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={t("dars.importSending", { pct })}>
            <div className="h-full rounded-full bg-leaf transition-[width] duration-150" style={{ width: `${pct}%` }} />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[0.88rem] tabular-nums text-muted">{t("dars.importSending", { pct })}</span>
            <Btn variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.88rem]"
              onClick={() => { annule.current = true; requete.current?.abort(); }}>
              {t("dars.importCancel")}
            </Btn>
          </div>
        </div>
      )}

      {erreur && <p role="alert" className="m-0 rounded-lg bg-err-bg px-3.5 py-2.5 text-[0.92rem] text-[#7a1f20]">{erreur}</p>}

      <div className="flex flex-wrap gap-2">
        <Btn type="submit" disabled={envoi || !fichier}>
          {envoi ? t("dars.importSubmitting") : t("dars.importSubmit")}
        </Btn>
      </div>
    </form>
  );
}

/** The single form: two tabs, a single save button. The server validates; its rejection
 *  message is displayed as-is. */
function Formulaire({ liste, depart, id, jourDe, onAnnuler, onEnregistre }: {
  liste: EventsList; depart: EventForm; id: string | null; jourDe: (n: string) => string;
  onAnnuler: () => void; onEnregistre: (texte: string) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [f, setF] = useState<EventForm>(depart);
  const [onglet, setOnglet] = useState("event");
  const [envoi, setEnvoi] = useState(false);
  const [erreur, setErreur] = useState("");
  const maj = (k: keyof EventForm) => (e: { target: { value: string } }) => setF((v) => ({ ...v, [k]: e.target.value }));

  const enregistrer = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setEnvoi(true);
    setErreur("");
    try {
      const r = id ? await api.majEvenement(id, f) : await api.creerEvenement(f);
      await onEnregistre(r.message || t("dars.saved"));
    } catch (e: any) {
      setErreur(e?.message || t("dars.saveError"));
    } finally {
      setEnvoi(false);
    }
  };

  return (
    <form onSubmit={enregistrer} className="grid gap-4 rounded-[10px] border border-line bg-white p-4.5">
      <h2 className="m-0 font-serif text-[1.35rem] font-normal text-deeper">{id ? t("dars.formEdit") : t("dars.formNew")}</h2>
      <div className="[&>div]:mb-0">
        <Segmented
          valeur={onglet}
          onChange={setOnglet}
          base="dars-form"
          items={[{ cle: "event", label: t("dars.tabs.event") }, { cle: "schedule", label: t("dars.tabs.schedule") }]}
        />
      </div>

      {onglet === "event" ? (
        <div className="grid gap-3.5 sm:grid-cols-2"
          role="tabpanel" id={idPanneau("dars-form", "event")} aria-labelledby={idOnglet("dars-form", "event")}>
          <Field label={t("dars.kind")}>
            <select className={inputClass} value={f.kind} onChange={maj("kind")}>
              {Object.entries(liste.kinds).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </Field>
          <Field label={t("dars.title_")}>
            <input className={inputClass} value={f.title} onChange={maj("title")} required maxLength={200} />
          </Field>
          <Field label={t("dars.speaker")}>
            <input className={inputClass} value={f.speaker} onChange={maj("speaker")} maxLength={120} />
          </Field>
          <Field label={t("dars.series")}>
            <input className={inputClass} value={f.series} onChange={maj("series")} maxLength={200} />
          </Field>
          <Field label={t("dars.session")}>
            <input className={inputClass} type="number" min={1} step={1} value={f.session} onChange={maj("session")} />
          </Field>
          <Field label={t("dars.maxMin")}>
            <input className={inputClass} type="number" min={1} step={1} value={f.max_min} onChange={maj("max_min")} />
          </Field>
          <label className="flex items-center gap-2 text-[0.92rem] font-semibold sm:col-span-2">
            <input type="checkbox" className="size-4 accent-[var(--color-deep)]" checked={f.publish === "1"}
              onChange={(e) => setF((v) => ({ ...v, publish: e.target.checked ? "1" : "0" }))} />
            {t("dars.publish")}
          </label>
        </div>
      ) : (
        <div className="grid gap-3.5 sm:grid-cols-2"
          role="tabpanel" id={idPanneau("dars-form", "schedule")} aria-labelledby={idOnglet("dars-form", "schedule")}>
          <div className="sm:col-span-2 [&>div]:mb-0">
            <span className="mb-1 block text-[0.9rem] font-semibold">{t("dars.mode")}</span>
            <Segmented
              valeur={f.mode}
              onChange={(m) => setF((v) => ({ ...v, mode: m }))}
              items={[{ cle: "weekly", label: t("dars.weekly") }, { cle: "once", label: t("dars.once") }]}
            />
          </div>
          {f.mode === "once" ? (
            <>
              <Field label={t("dars.date")}>
                <input className={inputClass} type="date" value={f.date} onChange={maj("date")} />
              </Field>
              <Field label={t("dars.time")}>
                <input className={inputClass} type="time" value={f.time} onChange={maj("time")} />
              </Field>
            </>
          ) : (
            <>
              <Field label={t("dars.weekday")}>
                <select className={inputClass} value={f.weekday} onChange={maj("weekday")}>
                  {liste.weekdays.map((j) => <option key={j} value={j}>{majuscule(jourDe(j))}</option>)}
                </select>
              </Field>
              <Field label={t("dars.reference")}>
                <select className={inputClass} value={f.after} onChange={maj("after")}>
                  <option value="">{t("dars.fixedTime")}</option>
                  {liste.prayers.map((p) => <option key={p} value={p}>{t("dars.afterPrayer")} {t(`dars.prayers.${p}`)}</option>)}
                </select>
              </Field>
              {f.after ? (
                <Field label={t("dars.offset")}>
                  <input className={inputClass} type="number" step={1} value={f.offset_min} onChange={maj("offset_min")} />
                </Field>
              ) : (
                <Field label={t("dars.time")}>
                  <input className={inputClass} type="time" value={f.time} onChange={maj("time")} />
                </Field>
              )}
            </>
          )}
        </div>
      )}

      {erreur && <p role="alert" className="m-0 rounded-lg bg-err-bg px-3.5 py-2.5 text-[0.92rem] text-[#7a1f20]">{erreur}</p>}
      <div className="flex flex-wrap gap-2">
        <Btn type="submit" disabled={envoi}>{envoi ? t("common.saving") : t("common.save")}</Btn>
        <Btn type="button" variante="ghost" onClick={onAnnuler}>{t("dars.cancel")}</Btn>
      </div>
    </form>
  );
}
