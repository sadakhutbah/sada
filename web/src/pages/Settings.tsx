import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Settings() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [onglet, setOnglet] = useState("mosque");
  useEffect(() => { api.settings().then(setD).catch(() => setD(null)); }, []);
  if (!d) return <p className="vide">{t("common.loading")}</p>;
  const m = d.mosque;

  return (
    <section className="carte">
      <h2>{t("settings.title")}</h2>
      <div className="segments rue">
        {d.sections.map((s: string) => (
          <button key={s} className={onglet === s ? "on" : ""} onClick={() => setOnglet(s)}>{t(`settings.tabs.${s}`)}</button>
        ))}
      </div>

      {onglet === "mosque" && (
        <form className="form une">
          <label>{t("settings.name")}<input defaultValue={m.name} readOnly /></label>
          <label>{t("settings.timezone")}<input defaultValue={m.timezone} readOnly /></label>
          <label>{t("settings.clipTitle")}<input defaultValue={m.clip_title} readOnly /></label>
        </form>
      )}
      {onglet === "live" && (
        <form className="form une">
          <label>{t("settings.orchestrator")}<input defaultValue={m.orchestrator_url} readOnly /></label>
        </form>
      )}
      {onglet === "folders" && (
        <form className="form une">
          <label>{t("settings.stateDir")}<input defaultValue={m.state_dir} readOnly /></label>
          <label>{t("settings.outputDir")}<input defaultValue={m.output_dir} readOnly /></label>
          <label>{t("settings.publishDir")}<input defaultValue={m.publish_dir} readOnly /></label>
        </form>
      )}
      {!["mosque", "live", "folders"].includes(onglet) && <p className="muted">{t("common.empty")}</p>}
      <p className="muted petite">Lecture seule pour l'instant : les formulaires d'écriture arrivent avec la suite du passage React.</p>
    </section>
  );
}
