import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type Session } from "../api";

export default function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const { t } = useTranslation();
  const [erreur, setErreur] = useState("");

  const envoyer = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErreur("");
    const formulaire = e.currentTarget;      // capturé avant l'attente : currentTarget est vidé après le rendu
    const saisie = new FormData(formulaire);
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
  };

  return (
    <div className="carte centre">
      <h1>{t("login.title")}</h1>
      <form onSubmit={envoyer} className="form">
        <label>{t("login.user")}<input name="username" autoComplete="username" required /></label>
        <label>{t("login.password")}<input name="password" type="password" autoComplete="current-password" required /></label>
        {erreur && <p className="err">{erreur}</p>}
        <button className="btn">{t("login.submit")}</button>
      </form>
    </div>
  );
}
