import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Pill, Tabs } from "../ui";
import { dateFr, libelles, liste, mmss } from "../format";

/** Review queue: one entry per clip, like the template back-office items.
 *  Title “Clip n · type vN”, then the context line, then the detection reason.
 *  The list arrives by pages of twenty: the archive grows with every khutba, and a page that renders
 *  three hundred entries takes visibly long to fill and to scroll. */

const TAILLE = 20;

export default function Clips() {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<any>(null);
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [statut, setStatut] = useState("");
  const [charge, setCharge] = useState(false);
  const debut = useRef(0);

  useEffect(() => {
    let vivant = true;
    debut.current = 0;
    setData(null);
    setItems([]);
    setTotal(0);
    setCharge(true);
    api.clips(statut, TAILLE, 0).then((r) => {
      if (!vivant) return;
      setData(r);
      setItems(liste<any>(r.items));
      setTotal(r.total ?? 0);
      debut.current = liste<any>(r.items).length;
    }).catch(() => {
      if (vivant) setData({ items: [], tabs: [], counts: {}, total: 0 });
    }).finally(() => { if (vivant) setCharge(false); });
    return () => { vivant = false; };
  }, [statut]);

  /** Next page: clips are appended, they do not replace. */
  const plus = async () => {
    setCharge(true);
    try {
      const r = await api.clips(statut, TAILLE, debut.current);
      const recus = liste<any>(r.items);
      setItems((l) => [...l, ...recus]);
      setTotal(r.total ?? total);
      debut.current += recus.length;
    } catch { /* on garde ce qui est affiché */ } finally { setCharge(false); }
  };

  const comptes = (data?.counts && typeof data.counts === "object" ? data.counts : {}) as Record<string, number>;
  const somme = Object.values(comptes).reduce((a: number, n: any) => a + Number(n), 0);
  const anciens = libelles(data?.tabs);
  const onglets = [
    { cle: "", label: t("clips.all"), compte: somme },
    ...liste<string>(data?.tabs).map((cle) => ({
      cle,
      label: anciens[cle] || t(`clips.statusTabs.${cle}`, { defaultValue: cle }),
      compte: Number(comptes[cle] ?? 0),
    })),
  ];

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("clips.title")}</h1>
        <p className="mt-1.5 mb-0 max-w-[70ch] text-[0.92rem] text-muted">{t("clips.intro")}</p>
      </div>

      <Tabs items={onglets} valeur={statut} onChange={setStatut} />

      <Card>
        {!data ? <p className="text-muted">{t("common.loading")}</p> : items.length === 0 ? (
          <p className="text-muted">{t("common.empty")}</p>
        ) : (
          <ul className="m-0 grid list-none p-0">
            {items.map((c: any) => (
              <li key={c.id} className="border-b border-line last:border-0">
                <Link
                  to={`/clips/${c.id}`}
                  className="flex items-start justify-between gap-4 py-3 no-underline hover:bg-paper-2/60"
                >
                <div>
                  <p className="m-0 text-[1rem] font-bold text-ink">
                    {t("clips.clip")} {c.idx}
                    {" · "}
                    {String(t(`clips.kinds.${c.kind}`, { defaultValue: c.kind }))} v{c.version}
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

      {/* Remaining rows come from the server's count, never from a guess. */}
      {items.length > 0 && items.length < total && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-[0.85rem] tabular-nums text-muted">
            {t("clips.shown", { n: items.length, total })}
          </span>
          <Btn variante="ghost" disabled={charge} onClick={plus}>
            {charge ? t("common.loading") : t("clips.loadMore", { n: Math.min(TAILLE, total - items.length) })}
          </Btn>
        </div>
      )}
    </div>
  );
}
