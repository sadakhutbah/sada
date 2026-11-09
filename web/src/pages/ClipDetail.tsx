import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, Kicker, Pill, Segmented, inputClass } from "../ui";
import { dateFr, mmss } from "../format";

/** Fiche d'un clip : la vidéo, la décision, les sous-titres, la publication et l'historique.
 *  Quatre blocs sur un seul écran serait trop haut : ils sont derrière des sous-onglets, la vidéo
 *  restant visible en haut. Reprise fidèle de la page « clip » du back-office en gabarits. */
export default function ClipDetail() {
  const { id } = useParams();
  const { t, i18n } = useTranslation();
  const cid = Number(id);
  const [d, setD] = useState<any>(null);
  const [onglet, setOnglet] = useState("clip");
  const [message, setMessage] = useState<{ texte: string; ko: boolean } | null>(null);
  const [occupe, setOccupe] = useState(false);

  const recharger = () =>
    api.clip(cid).then(setD).catch((e) => setMessage({ texte: e.message, ko: true }));

  useEffect(() => {
    recharger();
  }, [cid]);

  // Régénération : la vidéo est refaite côté serveur (environ une minute), la fiche se rafraîchit seule.
  const enCours = d?.clip?.regen_state === "en_cours";
  useEffect(() => {
    if (!enCours) return;
    const minuteur = window.setTimeout(recharger, 4000);
    return () => window.clearTimeout(minuteur);
  }, [enCours, d]);

  const agir = async (action: () => Promise<any>, succes: string) => {
    setOccupe(true);
    setMessage(null);
    try {
      await action();
      setMessage({ texte: succes, ko: false });
      await recharger();
    } catch (e: any) {
      setMessage({ texte: e.message ?? String(e), ko: true });
    } finally {
      setOccupe(false);
    }
  };

  if (!d) {
    return message ? <p className="py-16 text-center text-live">{message.texte}</p>
                   : <p className="py-16 text-center text-muted">{t("common.loading")}</p>;
  }

  const c = d.clip;
  const libelles = d.status_labels ?? {};
  const decides = d.history ?? [];

  return (
    <div className="grid gap-4">
      <div>
        <p className="m-0 mb-2 text-[0.88rem]">
          <Link to="/clips" className="text-deep underline decoration-leaf underline-offset-4">← {t("clips.title")}</Link>
          {" · "}
          <Link to={`/sermons/${d.sermon.id}`} className="text-deep underline decoration-leaf underline-offset-4">
            {dateFr(d.sermon.date, i18n.language)} · {d.sermon.label}
          </Link>
        </p>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">
            {t("clips.clip")} {c.idx} · {t(`clips.kinds.${c.kind}`)}
          </h1>
          <Pill statut={c.status} label={libelles[c.status] ?? c.status} />
        </div>
        <p className="mt-1.5 mb-0 max-w-[80ch] text-[0.88rem] text-muted">
          {t("clips.detail.extract", { de: mmss(c.start), a: mmss(c.end), s: Math.round(c.end - c.start) })}
          {" · "}{t("clips.detail.version", { n: c.version })}
          {c.why ? ` · ${c.why}` : ""}
        </p>
      </div>

      {message && (
        <p className={`m-0 rounded-lg px-3 py-2 text-[0.9rem] ${message.ko ? "bg-err-bg text-[#8a2219]" : "bg-paper-2 text-deep"}`}>
          {message.texte}
        </p>
      )}
      {c.regen_state === "erreur" && (
        <p className="m-0 rounded-lg bg-err-bg px-3 py-2 text-[0.9rem] text-[#8a2219]">
          {t("clips.detail.regenError")} : {c.regen_error}
        </p>
      )}
      {c.needs_regen && c.regen_state !== "en_cours" && (
        <p className="m-0 rounded-lg bg-err-bg px-3 py-2 text-[0.9rem] text-[#8a2219]">{t("clips.detail.needsRegen")}</p>
      )}

      <Card>
        {d.exists ? (
          <video
            key={c.version}
            controls
            preload="metadata"
            playsInline
            className="w-full rounded-lg bg-night"
            src={`/media/clip/${c.id}.mp4?v=${c.version}`}
          />
        ) : (
          <p className="m-0 text-muted">{t("clips.detail.noFile")}</p>
        )}
      </Card>

      <Segmented
        valeur={onglet}
        onChange={setOnglet}
        items={[
          { cle: "clip", label: t("clips.detail.decision") },
          { cle: "sous-titres", label: t("clips.detail.subtitles") },
          ...(d.can_publish ? [{ cle: "publication", label: t("clips.detail.publish") }] : []),
          { cle: "historique", label: t("clips.detail.history") },
        ]}
      />

      {onglet === "clip" && <Decision c={c} d={d} occupe={occupe} agir={agir} />}
      {onglet === "sous-titres" && <SousTitres d={d} agir={agir} />}
      {onglet === "publication" && d.can_publish && <Publication d={d} occupe={occupe} agir={agir} />}
      {onglet === "historique" && (
        <Card>
          {decides.length === 0 ? <p className="m-0 text-muted">{t("common.empty")}</p> : (
            <ul className="m-0 grid list-none gap-2 p-0">
              {decides.map((a: any) => (
                <li key={a.id} className="text-[0.93rem]">
                  <b>{(d.labels ?? {})[a.action] ?? a.action}</b> · {a.who ?? t("clips.detail.system")}
                  {" · "}<span className="text-muted">{(a.created_at ?? "").replace("T", " ").slice(0, 16)}</span>
                  {a.details && a.details !== "{}" && (
                    <><br /><span className="text-[0.85rem] text-muted">{a.details}</span></>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

function Decision({ c, d, occupe, agir }: { c: any; d: any; occupe: boolean; agir: any }) {
  const { t, i18n } = useTranslation();
  const [motif, setMotif] = useState("");
  const valider = c.status === "a_valider" && d.can_validate;

  return (
    <Card className="grid gap-4">
      <div>
        <Kicker>{t("clips.detail.decision")}</Kicker>
        {c.status === "publie" ? (
          <p className="m-0 text-[0.95rem]">
            {t("clips.detail.published", { n: c.version })}
            {c.decided_at ? ` — ${dateFr((c.decided_at ?? "").slice(0, 10), i18n.language)}` : ""}.
            {c.needs_regen ? ` ${t("clips.detail.publishedRegen", { n: c.version + 1 })}` : ""}
          </p>
        ) : c.status === "a_valider" ? (
          <p className="m-0 text-[0.92rem] text-muted">{t("clips.intro")}</p>
        ) : (
          <p className="m-0 text-[0.95rem]">
            {t("clips.detail.decided", { statut: d.status_labels?.[c.status] ?? c.status })}
            {c.reject_reason ? ` — ${c.reject_reason}` : ""}
          </p>
        )}
      </div>

      {c.status === "a_valider" && (
        <div className="grid gap-4">
          <Btn
            disabled={occupe || !valider}
            onClick={() => agir(() => api.decision(c.id, "valide"), t("clips.detail.validated"))}
          >
            {t("clips.detail.validate")}
          </Btn>
          {!valider && <p className="m-0 text-[0.85rem] text-muted">{t("clips.detail.validateBlocked")}</p>}
          <div>
            <Field label={t("clips.detail.reason")}>
              <input className={inputClass} value={motif} onChange={(e) => setMotif(e.target.value)} />
            </Field>
            <Btn
              variante="danger"
              className="mt-2"
              disabled={occupe}
              onClick={() => agir(() => api.decision(c.id, "rejete", motif), t("clips.detail.rejected"))}
            >
              {t("clips.detail.reject")}
            </Btn>
          </div>
        </div>
      )}

      {c.status !== "a_valider" && (
        <Btn
          variante="ghost"
          disabled={occupe}
          onClick={() => agir(() => api.decision(c.id, "rouvrir"), t("clips.detail.reopened"))}
        >
          {t("clips.detail.reopen")}
        </Btn>
      )}

      {(c.needs_regen || c.regen_state === "erreur") && (
        <div className="border-t border-line pt-3">
          <Kicker>{t("clips.detail.regenerate")}</Kicker>
          {d.can_regenerate ? (
            <Btn
              variante="leaf"
              disabled={occupe || c.regen_state === "en_cours"}
              onClick={() => agir(() => api.regenerer(c.id), t("clips.detail.regenStarted"))}
            >
              {t("clips.detail.regenerate")}
            </Btn>
          ) : (
            <p className="m-0 text-[0.85rem] text-muted">{d.regenerate_reason}</p>
          )}
        </div>
      )}
    </Card>
  );
}

function SousTitres({ d, agir }: { d: any; agir: any }) {
  const { t } = useTranslation();
  const [saisie, setSaisie] = useState<Record<number, string>>({});
  const lignes = useMemo(() => d.segments ?? [], [d]);

  return (
    <Card>
      <p className="m-0 mb-3 text-[0.85rem] text-muted">{t("clips.detail.subtitlesHelp")}</p>
      <ul className="m-0 grid list-none gap-3 p-0">
        {lignes.map((r: any) => (
          <li key={r.i} className="border-b border-line pb-3 last:border-0 last:pb-0">
            <p className="m-0 text-[0.78rem] tabular-nums text-muted">
              {mmss(r.rel)} · {r.verse ? r.verse.ref : t("clips.detail.line")}
              {r.corrected ? ` · ${t("clips.detail.corrected")}` : ""}
            </p>
            <p className="m-0 text-[0.95rem]" dir="auto">{r.text}</p>
            {r.verse && r.verse.fr && <p className="m-0 text-[0.9rem] text-muted" dir="auto">{r.verse.fr}</p>}
            {r.field && d.can_validate && (
              <div className="mt-1.5 flex items-center gap-2">
                <input
                  className={`${inputClass} py-1.5 text-[0.9rem]`}
                  dir="auto"
                  value={saisie[r.i] ?? (r.field === "fr" ? r.fr ?? "" : r.text)}
                  onChange={(e) => setSaisie({ ...saisie, [r.i]: e.target.value })}
                />
                <Btn
                  variante="ghost"
                  className="min-h-9 px-3 py-1 text-[0.85rem]"
                  onClick={() => agir(() => api.correction(d.sermon.id, r.i, r.field,
                    saisie[r.i] ?? (r.field === "fr" ? r.fr ?? "" : r.text)), t("clips.detail.saved"))}
                >
                  {t("common.save")}
                </Btn>
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Publication({ d, occupe, agir }: { d: any; occupe: boolean; agir: any }) {
  const { t, i18n } = useTranslation();
  const dispo = (d.connectors ?? []).filter((x: any) => x.available);
  const [dest, setDest] = useState(dispo[0]?.key ?? "local");
  const [legende, setLegende] = useState(d.caption ?? "");
  const pubs = d.publications ?? [];

  return (
    <Card className="grid gap-4">
      {d.can_publish ? (
        <>
          <div>
            <Field label={t("clips.detail.destination")}>
              <select className={inputClass} value={dest} onChange={(e) => setDest(e.target.value)}>
                {(d.connectors ?? []).map((x: any) => (
                  <option key={x.key} value={x.key} disabled={!x.available}>
                    {x.label}{x.available ? "" : ` — ${x.why}`}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label={t("clips.detail.caption")}>
            <textarea className={`${inputClass} min-h-28`} dir="auto" value={legende} onChange={(e) => setLegende(e.target.value)} />
          </Field>
          <Btn disabled={occupe || !dispo.length} onClick={() => agir(() => api.publier(d.clip.id, dest, legende), t("clips.detail.publishedOk"))}>
            {t("clips.detail.publishBtn")}
          </Btn>
        </>
      ) : (
        <p className="m-0 text-muted">{t("clips.detail.publishBlocked")}</p>
      )}

      {pubs.length > 0 && (
        <div className="border-t border-line pt-3">
          <Kicker>{t("clips.detail.publications")}</Kicker>
          <ul className="m-0 grid list-none gap-1.5 p-0 text-[0.9rem]">
            {pubs.map((p: any) => (
              <li key={p.id}>
                <Pill statut={p.status === "publie" ? "publie" : "rejete"} label={p.status} /> {p.connector} · v{p.clip_version}
                {" · "}{dateFr((p.created_at ?? "").slice(0, 10), i18n.language)}
                <br /><span className="text-muted">{p.location || p.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
