/** Onglets : les identifiants partagés par une barre d'onglets (ui.tsx) et le
 *  panneau qu'elle commande (les pages). Séparés des composants pour que le
 *  rechargement à chaud du front garde un fichier qui n'exporte que des composants. */

export type Onglet = { cle: string; label: string; compte?: number; eteint?: boolean };

/** Une `cle` peut venir du serveur : on la réduit à de quoi faire un id. */
const cleId = (cle: string) => (cle || "tous").replace(/[^a-zA-Z0-9_-]+/g, "-");

/** Id du bouton d'onglet : c'est lui que le panneau cite dans aria-labelledby. */
export const idOnglet = (base: string, cle: string) => `${base}-onglet-${cleId(cle)}`;

/** Id du panneau : c'est lui que l'onglet cite dans aria-controls. */
export const idPanneau = (base: string, cle: string) => `${base}-panneau-${cleId(cle)}`;
