import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card, Field, Segmented, Title, inputClass } from "../ui";

/** Un dossier vide n'est pas une case blanche : c'est le dossier par défaut du programme. */
function Chemin({ valeur }: { valeur?: string }) {
  const { t } = useTranslation();
  if (valeur) return <input className={inputClass} defaultValue={valeur} readOnly />;
  return <span className="rounded-lg border border-dashed border-[#bfcbb6] bg-paper-2 px-3 py-2.5 text-[0.92rem] text-muted">{t("settings.defaultFolder")}</span>;
}

export default function Settings() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [onglet, setOnglet] = useState("mosque");

  useEffect(() => { api.settings().then(setD).catch(() => setD(null)); }, []);

  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;
  const m = d.mosque;

  const onglets = d.sections.map((s: string) => ({ cle: s, label: t(`settings.tabs.${s}`) }));

  const contenu: Record<string, React.ReactNode> = {
    mosque: (
      <div className="grid max-w-[440px] gap-3.5">
        <Field label={t("settings.name")}><input className={inputClass} defaultValue={m.name} readOnly /></Field>
        <Field label={t("settings.timezone")}><input className={inputClass} defaultValue={m.timezone} readOnly /></Field>
        <Field label={t("settings.clipTitle")}><input className={inputClass} defaultValue={m.clip_title} readOnly /></Field>
      </div>
    ),
    live: (
      <div className="grid max-w-[440px] gap-3.5">
        <Field label={t("settings.orchestrator")}><input className={inputClass} defaultValue={m.orchestrator_url ?? ""} readOnly /></Field>
      </div>
    ),
    folders: (
      <div className="grid max-w-[440px] gap-3.5">
        <Field label={t("settings.stateDir")} aide={t("settings.foldersHint")}><Chemin valeur={m.state_dir} /></Field>
        <Field label={t("settings.outputDir")}><Chemin valeur={m.output_dir} /></Field>
        <Field label={t("settings.publishDir")}><Chemin valeur={m.publish_dir} /></Field>
      </div>
    ),
  };

  return (
    <Card>
      <Title grand>{t("settings.title")}</Title>
      <Segmented items={onglets} valeur={onglet} onChange={setOnglet} />
      {/* key = onglet : les champs sont remontés, sinon React garde les valeurs de l'onglet précédent */}
      <div key={onglet}>{contenu[onglet] ?? <p className="text-muted">{t("settings.soon")}</p>}</div>
      <p className="mt-4 mb-0 text-[0.82rem] text-muted">{t("settings.readonly")}</p>
    </Card>
  );
}
