import i18n from "i18next";
import { initReactI18next } from "react-i18next";

/** Textes de l'interface. Les adresses et les données restent identiques dans toutes les langues ;
 *  seuls les libellés changent. `ar` est prévu pour les mosquées arabophones (sens de lecture à droite).
 *  Les formulations reprennent mot pour mot celles du back-office en gabarits : c'est la référence. */
const fr = {
  app: { name: "Minbar Live", office: "back-office" },
  nav: {
    dashboard: "Tableau de bord", sermons: "Khutbas", clips: "Clips", publications: "Publication",
    settings: "Réglages", users: "Comptes", audit: "Journal", logout: "Déconnexion",
  },
  common: { loading: "Chargement…", empty: "Rien à afficher.", save: "Enregistrer", close: "Fermer", error: "Erreur", browse: "Parcourir…" },
  footer: "back-office",
  login: {
    title: "Connexion", user: "Identifiant", password: "Mot de passe", submit: "Entrer",
    mosque: "Choisir la mosquée", bad: "Identifiant ou mot de passe incorrect.", locked: "Trop de tentatives. Réessayez dans quelques minutes.",
  },
  dashboard: {
    pending: "Clips à valider", ready: "Prêts à publier", sermons: "Khutbas archivées",
    live: "Live du jour", recent: "Dernières khutbas", commands: "Dernières commandes du live",
    liveOn: "En direct", liveOff: "Hors ligne", unreachable: "Injoignable", noCommands: "Aucune commande manuelle.",
    janaza: "Déclarer une janaza", janazaName: "Nom du défunt", janazaInfo: "Mention (ex. 67 ans)",
    janazaSend: "Déclarer la janaza", more5: "+5 min", stop: "Arrêter", cancelDay: "Annuler aujourd'hui",
  },
  sermons: {
    title: "Khutbas", date: "Date", label: "Passage", imam: "Imam", clips: "Clips", pending: "À valider",
    theme: "Thème", open: "Ouvrir",
  },
  clips: {
    title: "File de validation des clips",
    intro: "Chaque clip est découpé automatiquement puis relu : validation, rejet motivé ou régénération.",
    sermon: "Khutba", kind: "Type", status: "Statut", duration: "Durée", why: "Raison",
    all: "Tous", clip: "Clip", kinds: { verset: "verset", conclusion: "conclusion" },
  },
  publications: { title: "Publication", connector: "Connecteur", enabled: "Activé", configured: "Réglé" },
  settings: {
    title: "Réglages",
    tabs: { mosque: "Mosquée", logo: "Logo", live: "Live", folders: "Dossiers", model: "Modèle", connectors: "Publication", access: "Accès" },
    name: "Nom de la mosquée", timezone: "Fuseau horaire", clipTitle: "Titre des clips", orchestrator: "Adresse de l'orchestrateur",
    foldersTitle: "Dossiers sur cette machine",
    foldersHint: "Choisissez avec le bouton Parcourir… : la fenêtre montre les dossiers réels de la machine qui héberge l'application, aucun chemin à taper. Un champ laissé vide garde le dossier par défaut du programme.",
    shortState: "fichiers de suivi des lives",
    shortOutput: "transcriptions, clips",
    shortPublish: "clips publiés en local",
    stateDir: "États des runs", outputDir: "Sorties du pipeline", publishDir: "Dossier de publication",
    byDefault: "par défaut :",
    defaultFolder: "dossier par défaut du programme",
    soon: "Cet onglet arrive avec la suite du passage.",
    readonly: "Lecture seule pour l'instant : les formulaires d'écriture arrivent avec la suite du passage React.",
  },
  users: { title: "Comptes", user: "Identifiant", name: "Nom affiché", role: "Rôle", active: "Actif", last: "Dernière connexion" },
  audit: { title: "Journal", when: "Quand", who: "Qui", action: "Action", object: "Objet", ip: "Adresse" },
};

const en: typeof fr = {
  app: { name: "Minbar Live", office: "back office" },
  nav: {
    dashboard: "Dashboard", sermons: "Sermons", clips: "Clips", publications: "Publishing",
    settings: "Settings", users: "Users", audit: "Log", logout: "Sign out",
  },
  common: { loading: "Loading…", empty: "Nothing to show.", save: "Save", close: "Close", error: "Error", browse: "Browse…" },
  footer: "back office",
  login: {
    title: "Sign in", user: "Username", password: "Password", submit: "Sign in",
    mosque: "Choose the mosque", bad: "Wrong username or password.", locked: "Too many attempts. Try again in a few minutes.",
  },
  dashboard: {
    pending: "Clips to review", ready: "Ready to publish", sermons: "Archived sermons",
    live: "Today's live", recent: "Latest sermons", commands: "Latest live commands",
    liveOn: "Live", liveOff: "Off air", unreachable: "Unreachable", noCommands: "No manual command.",
    janaza: "Declare a janaza", janazaName: "Name of the deceased", janazaInfo: "Note (e.g. 67 years old)",
    janazaSend: "Declare the janaza", more5: "+5 min", stop: "Stop", cancelDay: "Cancel today",
  },
  sermons: {
    title: "Sermons", date: "Date", label: "Slot", imam: "Imam", clips: "Clips", pending: "To review",
    theme: "Theme", open: "Open",
  },
  clips: {
    title: "Clip review queue",
    intro: "Each clip is cut automatically then reviewed: approve, reject with a reason, or regenerate.",
    sermon: "Sermon", kind: "Kind", status: "Status", duration: "Length", why: "Reason",
    all: "All", clip: "Clip", kinds: { verset: "verse", conclusion: "conclusion" },
  },
  publications: { title: "Publishing", connector: "Connector", enabled: "Enabled", configured: "Configured" },
  settings: {
    title: "Settings",
    tabs: { mosque: "Mosque", logo: "Logo", live: "Live", folders: "Folders", model: "Model", connectors: "Publishing", access: "Access" },
    name: "Mosque name", timezone: "Time zone", clipTitle: "Clip title", orchestrator: "Orchestrator address",
    foldersTitle: "Folders on this machine",
    foldersHint: "Pick with the Browse… button: the window shows the real folders of the machine hosting the application, no path to type. A field left empty keeps the program's default folder.",
    shortState: "live tracking files",
    shortOutput: "transcripts, clips",
    shortPublish: "clips published locally",
    stateDir: "Run state", outputDir: "Pipeline output", publishDir: "Publishing folder",
    byDefault: "default:",
    defaultFolder: "program's default folder",
    soon: "This tab arrives with the rest of the migration.",
    readonly: "Read-only for now: the write forms arrive with the rest of the React migration.",
  },
  users: { title: "Users", user: "Username", name: "Display name", role: "Role", active: "Active", last: "Last sign-in" },
  audit: { title: "Log", when: "When", who: "Who", action: "Action", object: "Object", ip: "Address" },
};

export const LANGUAGES = [
  { code: "fr", label: "Français", dir: "ltr" },
  { code: "en", label: "English", dir: "ltr" },
];

const choisi = typeof localStorage !== "undefined" ? localStorage.getItem("minbar.lang") : null;

i18n.use(initReactI18next).init({
  resources: { fr: { translation: fr }, en: { translation: en } },
  lng: choisi ?? "fr",
  fallbackLng: "fr",
  interpolation: { escapeValue: false },
});

export function setLanguage(code: string) {
  i18n.changeLanguage(code);
  localStorage.setItem("minbar.lang", code);
  const lang = LANGUAGES.find((l) => l.code === code);
  document.documentElement.lang = code;
  document.documentElement.dir = lang?.dir ?? "ltr";
}

export default i18n;
