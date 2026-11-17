import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api, type MosqueChoice, type PlatformMosque, type Session } from "../api";
import { Btn, Dialogue, Title } from "../ui";
import { OrchestrateurForm } from "./PlatformSettings";

/** Shown after signing in, before any mosque is open: choose one (or enter it as support).
 *  The operator also reaches the platform's own settings from here, and each mosque's
 *  orchestrator connection, without opening the mosque. */
export default function MosquePicker({
  session, onPick, onLogout,
}: { session: Session; onPick: (s: Session) => void; onLogout: () => void }) {
  const { t } = useTranslation();
  const [erreur, setErreur] = useState("");
  const [envoi, setEnvoi] = useState<number | null>(null);
  const operateur = !!session.user.operator;
  const navigate = useNavigate();
  const [connexions, setConnexions] = useState<Record<number, PlatformMosque>>({});
  const [orchestre, setOrchestre] = useState<PlatformMosque | null>(null);
  useEffect(() => {
    if (!operateur) return;
    api.platformMosques()
      .then((r) => setConnexions(Object.fromEntries(r.mosques.map((m) => [m.id, m]))))
      .catch(() => setConnexions({}));
  }, [operateur]);
  const toutes = session.mosques ?? [];
  const [filtre, setFiltre] = useState("");
  const q = filtre.trim().toLowerCase();
  const liste = q ? toutes.filter((m) => `${m.name} ${m.slug}`.toLowerCase().includes(q)) : toutes;

  const choisir = async (m: MosqueChoice) => {
    setErreur("");
    setEnvoi(m.id);
    try {
      onPick(await api.selectMosque(m.id));
    } catch (e) {
      setErreur(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setEnvoi(null);
    }
  };

  // A non-operator with a single mosque has nothing to choose: open it straight away (once).
  const auto = useRef(false);
  useEffect(() => {
    if (!operateur && toutes.length === 1 && !auto.current) {
      auto.current = true;
      void choisir(toutes[0]);
    }
  }, []); // eslint-disable-line

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-[520px]">
        <div className="mb-5 text-center">
          <img src="/sada-mark-encre.svg" alt="" className="mx-auto mb-3 h-[64px] w-auto" />
          <p className="font-serif text-[2rem] leading-none text-deeper">{t("app.name")}</p>
        </div>
        <section className="rounded-[10px] border border-line bg-white p-6.5">
          <h1 className="mb-1 font-serif text-[1.55rem] font-normal text-deeper">
            {operateur ? t("picker.operatorTitle") : t("picker.title")}
          </h1>
          <p className="mb-4 text-[0.9rem] text-muted">
            {operateur ? t("picker.operatorHint", { name: session.user.name }) : t("picker.hint", { name: session.user.name })}
          </p>
          {toutes.length >= 5 && (
            <input
              type="search"
              value={filtre}
              onChange={(e) => setFiltre(e.target.value)}
              aria-label={t("picker.search")}
              placeholder={t("picker.search")}
              className="mb-3 w-full rounded-lg border border-line bg-white px-3 py-2 text-[0.9rem] text-deeper outline-none focus:border-leaf"
            />
          )}
          {toutes.length === 0 && <p className="text-muted">{t("picker.none")}</p>}
          {toutes.length > 0 && liste.length === 0 && <p className="text-muted">{t("picker.noMatch")}</p>}
          <ul className="grid max-h-[320px] gap-1.5 overflow-y-auto pr-1">
            {liste.map((m) => (
              <li key={m.id} className="flex items-stretch gap-1.5">
                <button
                  type="button"
                  disabled={envoi !== null}
                  onClick={() => choisir(m)}
                  className="flex min-w-0 flex-1 cursor-pointer items-center justify-between gap-3 rounded-lg border border-line bg-white px-3 py-2 text-left transition-colors hover:border-leaf hover:bg-leaf/10 disabled:opacity-60"
                >
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate font-semibold text-deeper">{m.name}</span>
                    <span className="shrink-0 text-[0.8rem] text-muted">{m.slug}</span>
                  </span>
                  {m.access === "support" && (
                    <span className="shrink-0 rounded-full bg-gold/20 px-2.5 py-0.5 text-[0.75rem] font-bold text-deeper">
                      {t("picker.support")}
                    </span>
                  )}
                </button>
                {connexions[m.id] && (
                  <button
                    type="button"
                    onClick={() => setOrchestre(connexions[m.id])}
                    aria-label={t("picker.orchestratorFor", { name: m.name })}
                    className={`shrink-0 cursor-pointer rounded-lg border px-2.5 text-[0.8rem] font-semibold transition-colors hover:border-leaf hover:bg-leaf/10 ${
                      connexions[m.id].orchestrator_url ? "border-line bg-white text-deeper" : "border-gold bg-gold/10 text-deeper"
                    }`}
                  >
                    {t("picker.orchestrator")}
                  </button>
                )}
              </li>
            ))}
          </ul>
          {operateur && <p className="mt-3 text-[0.8rem] text-muted">{t("picker.supportNote")}</p>}
          {erreur && <p className="mt-3 rounded-lg bg-err-bg px-3 py-2 text-[0.9rem] text-[#7a1f20]">{erreur}</p>}
          <div className="mt-5 flex flex-wrap items-center gap-2">
            {operateur && (
              <Btn type="button" variante="ghost" onClick={() => navigate("/platform/settings")}>{t("platform.open")}</Btn>
            )}
            <Btn type="button" onClick={onLogout}>{t("nav.logout")}</Btn>
          </div>
        </section>
      </div>
      {orchestre && (
        <Dialogue label={t("picker.orchestratorTitle", { name: orchestre.name })} onFermer={() => setOrchestre(null)}
          boite="grid w-full max-w-[640px] gap-3 rounded-[10px] border border-line bg-white p-5">
          <Title>{t("picker.orchestratorTitle", { name: orchestre.name })}</Title>
          <OrchestrateurForm mosquee={orchestre}
            onSaved={(m) => setConnexions((x) => ({ ...x, [m.id]: m }))} />
          <div className="flex justify-end">
            <Btn type="button" variante="ghost" onClick={() => setOrchestre(null)}>{t("common.close")}</Btn>
          </div>
        </Dialogue>
      )}
    </div>
  );
}
