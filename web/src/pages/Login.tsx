import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Session } from "../api";
import { Btn, Field, inputClass } from "../ui";

export default function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const { t } = useTranslation();
  const [erreur, setErreur] = useState("");
  const [envoi, setEnvoi] = useState(false);

  const envoyer = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErreur("");
    setEnvoi(true);
    const formulaire = e.currentTarget;
    const saisie = new FormData(formulaire);
    try {
      const { pre } = await fetch("/api/v1/login-challenge", { credentials: "same-origin" }).then((r) => r.json());
      const corps = new URLSearchParams(saisie as unknown as Record<string, string>);
      corps.set("pre", pre);
      const r = await fetch("/api/v1/login", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: corps,
        credentials: "same-origin",
      });
      if (!r.ok) {
        setErreur(t(r.status === 429 ? "login.locked" : "login.bad"));
        return;
      }
      onLogin((await r.json()) as Session);
    } finally {
      setEnvoi(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-5 text-center">
          <p className="font-serif text-[2rem] leading-none text-deeper">{t("app.name")}</p>
          <p className="mt-1.5 text-[0.78rem] font-semibold uppercase tracking-[0.18em] text-green">{t("app.office")}</p>
        </div>
        <form onSubmit={envoyer} className="rounded-[10px] border border-line bg-white p-6.5">
          <h1 className="mb-4 font-serif text-[1.55rem] font-normal text-deeper">{t("login.title")}</h1>
          <div className="grid gap-3.5">
            <Field label={t("login.user")}>
              <input name="username" autoComplete="username" required className={inputClass} />
            </Field>
            <Field label={t("login.password")}>
              <input name="password" type="password" autoComplete="current-password" required className={inputClass} />
            </Field>
            {erreur && <p className="rounded-lg bg-err-bg px-3 py-2 text-[0.9rem] text-[#8a2219]">{erreur}</p>}
            <Btn type="submit" disabled={envoi}>{t("login.submit")}</Btn>
          </div>
        </form>
      </div>
    </div>
  );
}
