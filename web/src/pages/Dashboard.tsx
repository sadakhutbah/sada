import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api";
import { currentSession } from "../App";
import { Btn, Card, Kicker, Title } from "../ui";
import { dateFr, horloge, mmss } from "../format";

/** Tableau de bord : même ordre et mêmes blocs que le back-office en gabarits.
 *  Carte du live, puis les trois compteurs, puis les dernières khutbas et les dernières commandes. */
export default function Dashboard() {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [erreur, setErreur] = useState(false);

  useEffect(() => {
    api.dashboard().then(setD).catch(() => setErreur(true));
  }, []);

  if (erreur) return <p className="py-16 text-center text-muted">{t("common.error")}</p>;
  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  const peutPublier = currentSession()?.user.permissions.includes("publish") ?? false;
  const v = d.live ?? {};
  const horsService = v.ok === false;
  const etat = horsService
    ? t("dashboard.unreachable")
    : v.state_label ?? (v.state === "live" ? t("dashboard.liveOn") : t("dashboard.liveOff"));
  const pastille = horsService || v.state === "off" ? "bg-dot-off" : v.state === "live" ? "bg-live" : "bg-leaf";

  return (
    <div className="grid gap-4">
      <section className="rounded-[10px] bg-night p-5 text-night-ink">
        <Kicker sombre>{t("dashboard.live")}</Kicker>
        <p className="m-0 flex items-center gap-2.5 text-[1.55rem] font-bold leading-none text-white">
          <span className={`inline-block size-2.5 shrink-0 rounded-full ${pastille}`} />
          {etat}
        </p>
        {(v.error || v.last_reason) && <p className="m-0 mt-1.5 text-[0.92rem] text-night-muted">{v.error ?? v.last_reason}</p>}
        <p className="m-0 mt-1.5 text-[0.92rem] text-night-muted">
          {v.session ?? "—"} · <span className="tabular-nums">{horloge(v.clock)}</span>
        </p>

        {/* Commandes du live : mêmes boutons que les gabarits. Pas encore branchées côté React,
            elles restent visibles, désactivées, plutôt que de disparaître sans explication. */}
        <div className="mt-4 flex flex-wrap gap-2.5">
          <Btn variante="leaf" disabled title={t("settings.soon")}>{t("dashboard.more5")}</Btn>
          <Btn variante="danger" disabled title={t("settings.soon")}>{t("dashboard.stop")}</Btn>
          <Btn variante="light" disabled title={t("settings.soon")}>{t("dashboard.cancelDay")}</Btn>
        </div>
        {/* Fiche repliable de la janaza : fermée par défaut, comme les <details class="fiche"> des gabarits. */}
        <details className="group mt-3 rounded-[10px] border border-white/18 bg-white/5 px-3 py-2.5 [&>summary::-webkit-details-marker]:hidden">
          <summary className="cursor-pointer list-none text-[0.95rem] font-semibold text-white before:mr-1.5 before:font-normal before:text-leaf before:content-['+'] group-open:before:content-['−']">
            {t("dashboard.janaza")}
          </summary>
          <div className="mt-2.5 grid max-w-[420px] gap-2">
            <input className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white placeholder:text-night-muted"
                   placeholder={t("dashboard.janazaName")} disabled />
            <input className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white placeholder:text-night-muted"
                   placeholder={t("dashboard.janazaInfo")} disabled />
            <Btn variante="leaf" disabled title={t("settings.soon")} className="min-h-9 w-fit px-3 py-1.5 text-[0.9rem]">
              {t("dashboard.janazaSend")}
            </Btn>
          </div>
        </details>
      </section>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
        {[
          { k: "dashboard.pending", n: d.counts.pending, to: "/clips" },
          // « Prêts à publier » n'apparaît qu'à qui peut publier, comme dans les gabarits.
          ...(peutPublier ? [{ k: "dashboard.ready", n: d.counts.ready, to: "/publications" }] : []),
          { k: "dashboard.sermons", n: d.counts.sermons, to: "/khutbas" },
        ].map((s) => (
          <Link key={s.k} to={s.to} className="no-underline">
            <Card className="h-full transition-colors hover:border-leaf">
              <Kicker>{t(s.k)}</Kicker>
              <p className="m-0 font-serif text-[2.4rem] leading-none text-deeper">{s.n}</p>
            </Card>
          </Link>
        ))}
      </div>

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
                  <span className="block text-[0.95rem] font-semibold">{c.action.replace("live.", "")}</span>
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
