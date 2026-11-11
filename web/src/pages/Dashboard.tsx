import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api, ApiError, type Traitement } from "../api";
import { currentSession } from "../App";
import { Btn, Card, Kicker, Pill, Title } from "../ui";
import { dateFr, horloge, mmss } from "../format";

/** Length of a run, when it has both ends: minutes:seconds. */
function duree(it: Traitement): string {
  if (!it.debut || !it.fin) return "";
  const s = (Date.parse(it.fin) - Date.parse(it.debut)) / 1000;
  return Number.isFinite(s) && s >= 0 ? mmss(s) : "";
}

/** The server's error may quote a machine path (recording, output folder): the screen never shows
 *  one. `mot` is the stand-in word, translated by the caller. */
function sansChemin(texte: string, mot: string): string {
  return texte
    .replace(/[A-Za-z]:\\[^\s"']+/g, mot)
    .replace(/\/(?:home|mnt|tmp|var|usr|opt|srv|root|data)\/[^\s"',;:)]*/g, mot)
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** Dashboard: same order and same blocks as the template back-office.
 *  Live card, then the three counters, then the latest khutbas and the latest commands. */
export default function Dashboard() {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [erreur, setErreur] = useState(false);

  const [occupe, setOccupe] = useState(false);
  const [retour, setRetour] = useState<{ ok: boolean; texte: string } | null>(null);
  const [choixScene, setChoixScene] = useState("");
  const [nom, setNom] = useState("");
  const [mention, setMention] = useState("");
  /** null = panel hidden (no route yet on this back end). */
  const [traitements, setTraitements] = useState<Traitement[] | null>(null);
  const [relanceEnCours, setRelanceEnCours] = useState<string | null>(null);
  const [retourTraitement, setRetourTraitement] = useState<{ ok: boolean; texte: string } | null>(null);
  const [tourTraitements, setTourTraitements] = useState(0);
  const traitementsRef = useRef<Traitement[] | null>(null);

  useEffect(() => {
    api.dashboard().then(setD).catch(() => setErreur(true));
  }, []);

  /** Runs after the live, polled fast (10 s) while one waits or runs, slowly (60 s) otherwise.
   *  An older back end does not serve the route yet: 404 keeps the panel hidden, without an error. */
  useEffect(() => {
    let vivant = true;
    let minuteur = 0;
    const boucle = async () => {
      let items = traitementsRef.current;
      try {
        const r = await api.traitements();
        if (!vivant) return;
        items = Array.isArray(r?.items) ? r.items : [];
        traitementsRef.current = items;
        setTraitements(items);
      } catch (e) {
        if (!vivant) return;
        if (e instanceof ApiError && e.status === 404) {
          traitementsRef.current = null;
          setTraitements(null);
          minuteur = window.setTimeout(boucle, 60_000);   // retries once the route exists
          return;
        }
        // Any other hiccup: keep the last known list and try again shortly.
      }
      const enCours = items?.some((i) => i.etat === "attente" || i.etat === "en_cours") ?? false;
      minuteur = window.setTimeout(boucle, enCours ? 10_000 : 60_000);
    };
    boucle();
    return () => { vivant = false; window.clearTimeout(minuteur); };
  }, [tourTraitements]);

  /** Label of a command or choice: the translation is the only source. The server sends stable
   *  keys and values, never interface text. */
  const variante = (c: any): "leaf" | "light" | "danger" =>
    c.destructive ? "danger" : c.key === "start" ? "leaf" : "light";

  /** Sends a command to the live. The server answers with the re-read state: the card updates without a reload. */
  async function envoyer(c: any, corps: Record<string, string> = {}) {
    if (c.destructive && !window.confirm(t(`dashboard.confirm.${c.key}`))) return;
    setOccupe(true);
    setRetour(null);
    try {
      const r = await api.liveCommande(c.key, corps);
      setRetour({ ok: true, texte: r.message });
      setD((prev: any) => ({ ...prev, live: r.live ?? prev.live }));
      api.dashboard().then(setD).catch(() => {});   // command log up to date
    } catch (e: any) {
      setRetour({ ok: false, texte: e?.message ?? t("common.error") });
    } finally {
      setOccupe(false);
    }
  }

  /** Asks the server to run one processing again (failed or finished run). */
  async function relancer(it: Traitement) {
    const cle = `${it.run}:${it.session}`;
    setRelanceEnCours(cle);
    setRetourTraitement(null);
    const mot = t("dashboard.processing.hiddenPath");
    try {
      const r = await api.relancerTraitement(it.run, it.session);
      setRetourTraitement({ ok: true, texte: sansChemin(r?.message || t("dashboard.processing.relaunched"), mot) });
      setTourTraitements((n) => n + 1);            // reads the queue again right away
    } catch (e: any) {
      // The server's own message is already user-facing ("Relance impossible : …"): keep it,
      // and fall back to our sentence only when it has none.
      const brut = String(e?.message ?? "").trim();
      setRetourTraitement({
        ok: false,
        texte: brut
          ? sansChemin(brut, mot)
          : t("dashboard.processing.relaunchFailed", { message: t("common.error") }),
      });
    } finally {
      setRelanceEnCours(null);
    }
  }

  if (erreur) return <p className="py-16 text-center text-muted">{t("common.error")}</p>;
  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  const peutPublier = currentSession()?.user.permissions.includes("publish") ?? false;
  const peutAssigner = currentSession()?.user.permissions.includes("assign") ?? false;
  const v = d.live ?? {};
  const commandes: any[] = d.live_commands ?? [];
  const scene = commandes.find((c) => c.key === "scene");
  const janaza = commandes.find((c) => c.key === "janaza");
  const horsService = v.ok === false;
  const etat = horsService
    ? t("dashboard.unreachable")
    : (v.state && t(`dashboard.state.${v.state}`, { defaultValue: v.state })) || t("dashboard.liveOff");
  /** The current session comes structured ({n, total, planned}): the sentence is built here. */
  const session = v.session as { n?: number | null; total?: number; planned?: string | null } | null;
  const ligneSession = session
    ? `${session.n ? `${t("dashboard.sessionN", { n: session.n, total: session.total })} — ` : ""}${session.planned ?? ""}`
    : v.has_sessions ? t("dashboard.noSession") : "—";
  const pastille = horsService ? "bg-dot-off" : "bg-leaf";

  return (
    <div className="grid gap-4">
      <section className="rounded-[10px] bg-night p-5 text-night-ink">
        <Kicker sombre>{t("dashboard.live")}</Kicker>
        <p className="m-0 flex items-center gap-2.5 text-[1.55rem] font-bold leading-none text-white">
          <span className={`inline-block size-2.5 shrink-0 rounded-full ${pastille}`} />
          {etat}
        </p>
        {horsService ? (
          <p className="m-0 mt-1.5 text-[0.92rem] text-night-muted">{t("dashboard.unreachableHint")}</p>
        ) : (
          (v.error || v.last_reason) && <p className="m-0 mt-1.5 text-[0.92rem] text-night-muted">{v.error ?? v.last_reason}</p>
        )}
        {/* Nothing to say about the session when the orchestrator does not answer: the error message is enough. */}
        {!horsService && (
          <p className="m-0 mt-1.5 text-[0.92rem] text-night-muted">
            {ligneSession} · <span className="tabular-nums">{horloge(v.clock)}</span>
          </p>
        )}

        {/* Live commands: the list comes from the server (live_commands), with keys, values and a
            danger flag only. Commands with choices (extend, scene) expand; the janaza has its
            collapsible form. */}
        <div className="mt-4 flex flex-wrap items-center gap-2.5">
          {commandes.filter((c: any) => c.key !== "janaza" && c.key !== "scene").map((c: any) =>
            c.choices.length > 0 ? (
              c.choices.map((valeur: string) => (
                <Btn key={`${c.key}-${valeur}`} variante={variante(c)} disabled={occupe}
                     onClick={() => envoyer(c, { valeur })}>{t(`dashboard.choice.${valeur}`)}</Btn>
              ))
            ) : (
              <Btn key={c.key} variante={variante(c)} disabled={occupe} onClick={() => envoyer(c)}>{t(`dashboard.cmd.${c.key}`)}</Btn>
            ))}
        </div>
        {scene && (
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <label className="text-[0.92rem] text-night-muted" htmlFor="scene-live">{t(`dashboard.cmd.${scene.key}`)}</label>
            <select id="scene-live" value={choixScene} onChange={(e) => setChoixScene(e.target.value)}
                    className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white max-md:min-h-11 pointer-coarse:min-h-11">
              <option value="" className="text-ink">{t("dashboard.sceneChoose")}</option>
              {scene.choices.map((valeur: string) => <option key={valeur} value={valeur} className="text-ink">{t(`dashboard.choice.${valeur}`)}</option>)}
            </select>
            <Btn variante="light" disabled={occupe || !choixScene} className="min-h-9 px-3 py-1.5 text-[0.9rem] max-md:min-h-11 pointer-coarse:min-h-11"
                 onClick={() => envoyer(scene, { valeur: choixScene })}>{t("dashboard.sceneApply")}</Btn>
          </div>
        )}
        {retour && (
          <p role="status" className={`m-0 mt-3 text-[0.92rem] ${retour.ok ? "text-leaf" : "text-[#ff9b94]"}`}>{retour.texte}</p>
        )}
        {janaza && (
          <details className="group mt-3 rounded-[10px] border border-white/18 bg-white/5 px-3 py-2.5 [&>summary::-webkit-details-marker]:hidden">
            <summary className="cursor-pointer list-none text-[0.95rem] font-semibold text-white before:mr-1.5 before:font-normal before:text-leaf before:content-['+'] group-open:before:content-['−']">
              {t("dashboard.janaza")}
            </summary>
            <div className="mt-2.5 grid max-w-[420px] gap-2">
              <input className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white placeholder:text-night-muted max-md:min-h-11 pointer-coarse:min-h-11"
                     placeholder={t("dashboard.janazaName")} value={nom} maxLength={80} onChange={(e) => setNom(e.target.value)} />
              <input className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white placeholder:text-night-muted max-md:min-h-11 pointer-coarse:min-h-11"
                     placeholder={t("dashboard.janazaInfo")} value={mention} maxLength={60} onChange={(e) => setMention(e.target.value)} />
              <Btn variante="leaf" disabled={occupe} className="min-h-9 w-fit px-3 py-1.5 text-[0.9rem] max-md:min-h-11 pointer-coarse:min-h-11"
                   onClick={() => envoyer(janaza, { name: nom.trim(), info: mention.trim() })}>
                {t("dashboard.janazaSend")}
              </Btn>
            </div>
          </details>
        )}
      </section>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
        {[
          { k: "dashboard.pending", n: d.counts.pending, to: "/clips" },
          // “Ready to publish” only appears to those who can publish, as in the templates.
          ...(peutPublier ? [{ k: "dashboard.ready", n: d.counts.ready, to: "/publications" }] : []),
          { k: "dashboard.sermons", n: d.counts.sermons, to: "/khutbas" },
          ...(d.counts.dars ? [{ k: "dashboard.dars", n: d.counts.dars, to: "/dars" }] : []),
        ].map((s) => (
          <Link key={s.k} to={s.to} className="no-underline">
            <Card className="h-full transition-colors hover:border-leaf">
              <Kicker>{t(s.k)}</Kicker>
              <p className="m-0 font-serif text-[2.4rem] leading-none text-deeper">{s.n}</p>
            </Card>
          </Link>
        ))}
      </div>

      {/* Runs after the live: stays hidden when this back end does not serve the route. */}
      {traitements && (
        <Card>
          <Title>{t("dashboard.processing.title")}</Title>
          {traitements.length === 0 ? (
            <p className="m-0 text-[0.95rem] text-muted">{t("dashboard.processing.empty")}</p>
          ) : (
            <ul className="m-0 grid list-none p-0">
              {traitements.map((it) => {
                const dureeTxt = duree(it);
                const cle = `${it.run}:${it.session}`;
                return (
                  <li key={cle} className="border-b border-line py-2 last:border-0 last:pb-0">
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                      <span className="text-[0.95rem] font-semibold text-ink">
                        {it.label}
                        <span className="font-normal text-muted">
                          {" · "}{dateFr(it.date, i18n.language)}
                          {dureeTxt && ` · ${t("dashboard.processing.duration", { d: dureeTxt })}`}
                        </span>
                      </span>
                      <span className="flex items-center gap-2">
                        <Pill statut={it.etat}
                              label={t(`dashboard.processing.state.${it.etat}`, { defaultValue: it.etat })} />
                        {peutAssigner && (it.etat === "echec" || it.etat === "termine") && (
                          <Btn variante={it.etat === "echec" ? "dangerGhost" : "ghost"}
                               disabled={relanceEnCours === cle}
                               className="min-h-8 px-2.5 py-1 text-[0.85rem] max-md:min-h-11 pointer-coarse:min-h-11"
                               onClick={() => relancer(it)}>
                            {t("dashboard.processing.relaunch")}
                          </Btn>
                        )}
                      </span>
                    </div>
                    {it.erreur && (
                      <p className="m-0 mt-0.5 break-words text-[0.85rem] text-[#8a2219]">
                        {sansChemin(it.erreur, t("dashboard.processing.hiddenPath"))}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {retourTraitement && (
            <p role="status" className={`m-0 mt-2 text-[0.9rem] ${retourTraitement.ok ? "text-green" : "text-[#8a2219]"}`}>
              {retourTraitement.texte}
            </p>
          )}
        </Card>
      )}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-4">
        <Card>
          <Title>{t("dashboard.recent")}</Title>
          {d.recent.length === 0 ? <p className="text-muted">{t("common.empty")}</p> : (
            <ul className="m-0 grid list-none p-0">
              {d.recent.map((k: any) => (
                <li key={k.id} className="border-b border-line py-2.5 last:border-0 last:pb-0">
                  <Link to={`/khutbas/${k.id}`} className="block no-underline">
                    <span className="block text-[0.98rem] font-bold text-ink">{dateFr(k.date, i18n.language)}</span>
                    <span className="block text-[0.9rem] text-muted">
                      {k.label}
                      {k.duration_s ? ` · ${mmss(k.duration_s)}` : ""}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <Title>{t("dashboard.commands")}</Title>
          {d.commands.length === 0 ? <p className="text-muted">{t("dashboard.noCommands")}</p> : (
            <ul className="m-0 grid list-none p-0">
              {d.commands.map((c: any) => (
                <li key={c.id} className="border-b border-line py-2 last:border-0 last:pb-0">
                  <span className="block text-[0.95rem] font-semibold">
                    {(() => { const cle = c.action.replace("live.", ""); return t(`dashboard.cmd.${cle}`, { defaultValue: cle }); })()}
                  </span>
                  <span className="block text-[0.85rem] text-muted">
                    {c.who ?? "—"} · {dateFr(String(c.at ?? "").slice(0, 10), i18n.language)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
