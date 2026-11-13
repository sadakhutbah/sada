import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, inputClass } from "../ui";

/** Account created (or password reset) by an administrator: the person picks their own password
 *  before reaching anything else. The server refuses every right until then. */
export default function FirstPassword({ nom, onDone, onLogout }: { nom: string; onDone: () => void; onLogout: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ current: "", nouveau: "", confirm: "" });
  const [erreur, setErreur] = useState("");
  const [enCours, setEnCours] = useState(false);

  const valider = async (e: React.FormEvent) => {
    e.preventDefault();
    setErreur("");
    if (f.nouveau !== f.confirm) { setErreur(t("firstPassword.mismatch")); return; }
    setEnCours(true);
    try {
      await api.motDePasse(f.current, f.nouveau, f.confirm);
      onDone();
    } catch (err: any) {
      setErreur(err.message);
    } finally {
      setEnCours(false);
    }
  };

  return (
    <main className="mx-auto grid min-h-screen max-w-[460px] content-center px-4 py-10">
      <Card>
        <p className="m-0 mb-2 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-green">{t("firstPassword.kicker")}</p>
        <h1 className="m-0 mb-2 font-serif text-[1.8rem] font-normal leading-[1.15] text-deeper">{t("firstPassword.title", { nom })}</h1>
        <p className="m-0 mb-4 text-muted">{t("firstPassword.intro")}</p>
        <form className="grid gap-3.5" onSubmit={valider}>
          <Field label={t("firstPassword.current")} aide={t("firstPassword.currentHelp")}>
            <input className={inputClass} type="password" required autoComplete="current-password" value={f.current}
                   onChange={(e) => setF({ ...f, current: e.target.value })} />
          </Field>
          <Field label={t("firstPassword.new")} aide={t("firstPassword.newHelp")}>
            <input className={inputClass} type="password" required minLength={10} autoComplete="new-password" value={f.nouveau}
                   onChange={(e) => setF({ ...f, nouveau: e.target.value })} />
          </Field>
          <Field label={t("firstPassword.confirm")}>
            <input className={inputClass} type="password" required minLength={10} autoComplete="new-password" value={f.confirm}
                   onChange={(e) => setF({ ...f, confirm: e.target.value })} />
          </Field>
          {erreur && <p role="alert" className="m-0 text-[0.9rem] text-[#7a1f20]">{erreur}</p>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Btn type="submit" disabled={enCours}>{t("firstPassword.submit")}</Btn>
            <button type="button" onClick={onLogout} className="cursor-pointer border-0 bg-transparent text-muted underline">
              {t("firstPassword.logout")}
            </button>
          </div>
        </form>
      </Card>
    </main>
  );
}
