# Horaires de prière

Sadā a besoin des horaires pour armer le direct (khutba du vendredi, dars après une prière, tarawih) et pour ses écrans. Ils viennent d'une **source**, choisie dans Réglages › Prière (ou à la mise en route) :

| Source | Ce que vous faites | Page publique |
|---|---|---|
| **Mawaqit** | Rien à saisir : indiquez l'adresse ou l'identifiant Mawaqit de la mosquée. | Non : Sadā ne copie ni ne publie le calendrier Mawaqit. |
| **Calculés** | Méthode, coordonnées, décalages. Sadā calcule chaque jour. | Oui |
| **Saisis à la main** | Vous saisissez (ou importez en CSV) vos propres horaires. | Oui |

Si Mawaqit disparaît, rien ne s'arrête : choisissez « Calculés » ou « Saisis à la main ».

## Calculés

- **Méthode** : UOIF 12°/12°, Ligue islamique mondiale 18°/17°, ISNA 15°/15°, Égypte, Umm al-Qura, Karachi, Maroc, Diyanet, ou angles personnalisés (Fajr, Isha ; l'Isha peut aussi être un délai après le Maghrib).
- **Asr** : ombre ×1 (chaféite, malikite, hanbalite) ou ×2 (hanafite).
- **Hautes latitudes** : règle appliquée quand le soleil n'atteint pas l'angle (milieu de la nuit, septième de la nuit, selon l'angle).
- **Coordonnées** : latitude et longitude de la mosquée (le bouton « Ma position » les remplit).
- **Ajustements** : un décalage en minutes par prière, un délai d'iqama après l'adhan, et des heures limites (« l'Isha jamais avant 19:10 », « le Dhuhr jamais avant 13:00 »).
- **Un jour à corriger** : dans l'onglet Horaires, une case remplie remplace le calcul pour ce jour seulement ; en gris, le calcul.

L'aperçu montre le résultat avant d'enregistrer. Les calculs reposent sur la bibliothèque `praytimes` (algorithme de PrayTimes.org).

### Quitter Mawaqit sans changer d'horaires

Dans l'onglet Calcul, « Déduire depuis Mawaqit » lit une fois le calendrier Mawaqit de la mosquée, cherche la méthode, les décalages et les limites qui le reproduisent, et les propose avec l'écart maximal en minutes. Rien n'est conservé de Mawaqit. En ligne de commande : `python -m sada.prayer_fit <adresse Mawaqit> --year 2027`.

Les calendriers Mawaqit de beaucoup de mosquées sont eux-mêmes calculés (Fajr, lever, Dhuhr, Maghrib se retrouvent à la minute près) ; l'Asr diffère parfois d'une à deux minutes, et une mosquée qui saisit ses horaires à la main ne se reproduit que partiellement : l'écart annoncé le dit.

## Saisis à la main

- **Grille du mois** : une ligne par jour. Entrée et flèches changent de ligne ; coller des cellules d'un tableur remplit la grille. « Afficher l'iqama » ajoute les colonnes d'iqama ; « Remplir l'iqama » met adhan + N minutes.
- **CSV** (onglet Importer) : colonnes `date, fajr, sunrise, dhuhr, asr, maghrib, isha`, puis `fajr_iqama`… (`+10` = adhan + 10 min). Séparateur virgule, point-virgule ou tabulation ; dates `AAAA-MM-JJ` ou `JJ/MM/AAAA`. Tout est vérifié avant d'écrire : une erreur, et rien n'est importé. « Exporter l'année » donne un CSV que l'import relit : une année calculée devient ainsi un tableau à retoucher.

## Exporter : calendrier PDF et CSV

Onglet **Exporter** (mosquée calculée ou saisie) : un mois, une période « du … au … », le Ramadan (en cours, sinon le prochain, d'après le calendrier Umm al-Qura) ou l'année.

- **PDF** : un calendrier à imprimer, une page A4 portrait par mois civil. Aux couleurs et aux polices de Sada : le logo Sada, le nom de la mosquée et la période en en-tête (« Ramadan 1448 » pour le mois entier), une ligne par jour qui remplit la page (date, date de l'hégire, Fajr, Chourouk, Dhuhr, Asr, Maghrib, Isha) ; la ligne du vendredi est en évidence avec les horaires de Jumu'a à la place du Dhuhr, et pendant le Ramadan les nuits impaires des dix derniers jours sont signalées (la nuit 27 ressort ; une nuit commence au Maghrib de la veille). Le pied de page porte sadakhutbah.org et, quand les horaires sont publics, un QR code vers leur page publique. Un mois de l'hégire entier (le Ramadan) tient sur une page ; toute autre période a une page par mois civil. Les libellés suivent la langue de l'interface ; le PDF utilise une police latine : en arabe les libellés restent en anglais, et les caractères non latins d'un nom de mosquée sont remplacés par « ? ».
- **CSV** : les mêmes colonnes que l'import (`date, fajr, sunrise, …, isha_iqama`), relu tel quel par « Importer ». Une mosquée saisie n'exporte que les jours qu'elle a saisis.
- Pas de fichier `ics`.
- Une mosquée **Mawaqit** n'a pas d'export : Sada ne garde de Mawaqit que les horaires du jour (cache interne), jamais un calendrier, et ne les copie pas.

Par l'API (session administrateur) : `GET /api/v1/settings/prayer/export?format=pdf|csv` avec `month=2027-03`, ou `start=…&end=…`, ou `ramadan=true`, ou `year=2027` ; au plus 366 jours.

## Page publique (lecture seule)

Pour une mosquée **calculée** ou **manuelle** (case « Page publique des horaires ») :

```
GET /api/v1/public/mosques/<slug>/prayer-times               # aujourd'hui
GET /api/v1/public/mosques/<slug>/prayer-times?date=2027-03-21
GET /api/v1/public/mosques/<slug>/prayer-times?month=2027-03
GET /api/v1/public/mosques/<slug>/prayer-times/page?month=2027-03     # la page : un mois, avec les boutons PDF et CSV
GET /api/v1/public/mosques/<slug>/prayer-times/export?format=pdf&month=2027-03   # ou format=csv, start=…&end=…, ramadan=1
```

Sans connexion, uniquement les horaires (jamais une adresse ni un réglage). Lisible depuis n'importe quelle page web (CORS), mise en cache une minute, limitée à 60 requêtes par minute et par client (réponse 429 avec `Retry-After`). Une mosquée Mawaqit répond 404 avec l'adresse de sa page Mawaqit.
