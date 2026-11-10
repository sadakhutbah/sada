import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card, Pill, Tabs } from "../ui";
import { dateFr, mmss } from "../format";

/** Review queue: one entry per clip, like the template back-office items.
 *  Title “Clip n · type vN”, then the context line, then the detection reason. */
export default function Clips() {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<any>(null);
  const [statut, setStatut] = useState("");

  useEffect(() => {
    api.clips(statut).then(setData).catch(() => setData({ items: [], tabs: [], counts: {} }));
  }, [statut]);

  const comptes = data?.counts ?? {};
  const total = Object.values(comptes).reduce((a: number, n: any) => a + Number(n), 0);
  const onglets = [
    { cle: "", label: t("clips.all"), compte: total },
    ...(data?.tabs ?? []).map((cle: string) => ({ cle, label: t(`clips.statusTabs.${cle}`), compte: Number(comptes[cle] ?? 0) })),
  ];

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("clips.title")}</h1>
        <p className="mt-1.5 mb-0 max-w-[70ch] text-[0.92rem] text-muted">{t("clips.intro")}</p>
      </div>

      <Tabs items={onglets} valeur={statut} onChange={setStatut} />

      <Card>
        {!data ? <p className="text-muted">{t("common.loading")}</p> : data.items.length === 0 ? (
          <p className="text-muted">{t("common.empty")}</p>
        ) : (
          <ul className="m-0 grid list-none p-0">
            {data.items.map((c: any) => (
              <li key={c.id} className="border-b border-line last:border-0">
                <Link
                  to={`/clips/${c.id}`}
                  className="flex items-start justify-between gap-4 py-3 no-underline hover:bg-paper-2/60"
                >
                <div>
                  <p className="m-0 text-[1rem] font-bold text-ink">
                    {t("clips.clip")} {c.idx}
                    {" · "}
                    {t(`clips.kinds.${c.kind}`)} v{c.version}
                  </p>
                  <p className="m-0 text-[0.88rem] text-muted">
                    {dateFr(c.date, i18n.language)} · {c.sermon} ·{" "}
                    <span className="tabular-nums">
                      {mmss(c.start)}–{mmss(c.end)} ({Math.round(c.end - c.start)} s)
                    </span>
                  </p>
                  {c.why && <p className="m-0 text-[0.88rem] text-muted">{c.why}</p>}
                </div>
                  <Pill statut={c.status} label={t(`clips.statuses.${c.status}`)} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
