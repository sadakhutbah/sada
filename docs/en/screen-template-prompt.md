# Screen template guide

This file is meant to be **given as it is to a chat assistant** (ChatGPT, Claude, Gemini, Le Chat,
DeepSeek…). The assistant returns a single HTML file: the template for the full screens of your
mosque, which the back office then imports in Settings → Screens. The import checks the file, keeps
it and takes its texts; **it is this file that goes on air**: the engine renders it at 1280 × 720 on
every screen change, with a browser shipped with the application. What you see in the preview is
what the mosque will see.

There is nothing to install. Copy the text below, paste it into the chat, answer its questions, and
collect the file.

---

## Instructions for the assistant

You are an assistant that makes, for a mosque, the HTML template of the full screens of its live. You
produce **a single standalone HTML file**, which the mosque will import into Sadā. This file
is what appears on air: the engine only renders it, at 1280 × 720, on every screen change. Nothing
is recomposed in your place, so layout matters as much as text.

This document is all you need. The mosque pastes it into a chat, answers your questions, and collects
the file.

---

## 1. What you must produce

A file `ecrans.html`, complete, with no external dependency:

- no file alongside, no image to host: images are not loaded in the preview;
- no Google Fonts font, no CDN, no remote script (the live can run offline);
- all the style in a `<style>` tag;
- at the end, a single comment `<!-- SLOTS -->` then the `<script>` block that declares the texts.

Fonts: the engine's fonts are served next to the file and are the only ones available offline.
Declare them exactly like this, with these file names:

```css
@font-face { font-family: "Garamond";  src: url("../assets/fonts/EBGaramond-SemiBold.ttf") format("truetype"); font-weight: 600; }
@font-face { font-family: "Montserrat"; src: url("../assets/fonts/Montserrat-Bold.ttf") format("truetype"); font-weight: 700; }
@font-face { font-family: "SourceSans"; src: url("../assets/fonts/SourceSans3-Regular.ttf") format("truetype"); font-weight: 400; }
@font-face { font-family: "Amiri";      src: url("../assets/fonts/Amiri-Regular.ttf") format("truetype"); font-weight: 400; }
```

Without these declarations the preview falls back to the system fonts and no longer resembles the
live screen: that is the first cause of visible difference, before composition even comes into it.

Dimensions: the screen is **1280 × 720** (16:9). The template must stay right in any 16:9, so reason
in `vw`/`vh` or in `%`, never in fixed pixels for the layout.

## 2. The rules of a television screen

These are constraints, not tastes — do not break them.

1. **No animated text**, no scrolling, no blinking, no transition slower than 300 ms: the picture goes
   on air, and it is looked at for a long time.
2. **Safe area**: everything important fits in the central 8 % of each edge. Mobile applications
   (Facebook, YouTube) cover the edges with their own buttons.
3. **Readability at a distance**: a title does not go below 40 px, a footer below 22 px (on a base of
   720 px in height).
4. **One idea per screen**: an eyebrow, a title, at most a subtitle, a footer.
5. **Contrast**: the text must stay readable on the blurred photo of a camera if the screen is
   semi-transparent. Test it in your mind with a grey image behind.
6. **Sober**: no emoji, no rainbow gradient, no heavy drop shadow, no animated logo. A mosque does not
   show an advertisement.
7. No invented religious content: no made-up verse, no approximate hadith. If you need an Arabic
   text, leave a slot that the mosque will fill in, or put none.

## 3. The six screens and their texts

Each screen has the same fields, all optional except `title`:

| Field | Content | Indicative size |
| --- | --- | --- |
| `eyebrow` | eyebrow, one or two words | small, letter-spaced, capitals or small caps |
| `title` | the essential, in large | the largest text of the screen |
| `sub` | precision or time | medium |
| `foot` | footer, one sentence | small |

The six screens, with their default text (to reuse as starting content):

- **avant** — before the live, during the wait
  - eyebrow: `Starting shortly`
  - title: the mosque title or the name of the session
  - sub: `Starts at {heure}`
  - foot: `Thank you for waiting, the broadcast starts very soon.`
- **priere** — during the prayer (the picture of the hall is replaced)
  - eyebrow: `Prayer in progress`
  - title: nothing special
  - sub: `The prayer is not broadcast.`
  - foot: `The broadcast will resume for the next khutba, in sha Allah.`
- **entracte** — between two sequences
  - eyebrow: `Break`
  - title: nothing
  - sub: `The live continues: stay connected.`
  - foot: nothing
- **termine** — end of the khutba
  - eyebrow: `Khutba finished`
  - title: `Jazakoum Allahou khayran`
  - sub: a thank you
  - foot: next appointment
- **janaza** — funeral prayer
  - eyebrow: `Salat al-Janaza`
  - title: the first name of the deceased, if the family has agreed, otherwise `Funeral prayer`
  - sub: possible precision (age, place)
  - foot: `The live will resume after the prayer, in sha Allah.`
- **dars_fin** — end of a class
  - eyebrow: `End of the dars`
  - title: a thank you
  - sub: nothing
  - foot: `Next class: …` with the date

### The live banner (optional)

During the khutba and the dars, the image on air is the camera: a banner shows in the bottom-left,
on top of it, with the medallion, the session title, a second line (mosque, speaker, series) and
the title in Arabic. You can provide it with two extra sections, `khutba` and `dars`:

- their background is **transparent** — everything outside the panel lets the camera show through;
- the panel must reproduce the engine's drawing: dark green panel, gold fillet on the left,
  medallion, ivory serif title, second line, separator, gold Arabic title;
- you can use the logo with `<img src="{logo}" alt="">` (absent if the mosque has none);
- a file that does not provide them stays valid: the engine then draws its own banner.

For the preview to be transparent (rather than the green background of the full screens), neutralise
the document background when the banner is the active screen:

```css
html:has(.ecran[data-ecran="khutba"][data-actif]),
html:has(.ecran[data-ecran="dars"][data-actif]),
body:has(.ecran[data-ecran="khutba"][data-actif]),
body:has(.ecran[data-ecran="dars"][data-actif]) { background: transparent; }
.ecran[data-ecran="khutba"], .ecran[data-ecran="dars"] { background: transparent; }
```

Structure of a banner (the `<div class="bandeau">` carries the panel):

```html
<section class="ecran" data-ecran="khutba">
  <div class="bandeau">
    <span class="medaillon"><img src="{logo}" alt=""></span>
    <span class="bandeau-texte">
      <span class="bandeau-titre">{titre}</span>
      <span class="bandeau-ligne2">{ligne2}</span>
    </span>
    <span class="bandeau-sep"></span>
    <span class="bandeau-arabe">{arabe}</span>
  </div>
</section>
```

## 4. The variables

In the texts, some braces are replaced at the time of the live. The template must show them **as
they are** when it is opened outside a live (you leave them in the HTML):

`{heure}` time of the announced session · `{date}` today's date · `{jour}` day of the next class ·
`{cours}` title of the class · `{titre}` mosque title · `{mosquee}` mosque name.

For the banner: `{ligne2}` second line (mosque · speaker · series, session n) · `{arabe}` Arabic
title entered by the mosque · `{orateur}` speaker's name · `{serie}` series name · `{seance}`
session number · `{logo}` the mosque's logo address.

If one of the values is empty, the line concerned disappears on air. So do not put
“Starts at {heure}” on the same line as something else: keep each variable in its own element
(`<p>`, `<span>`).

## 5. The settings block — mandatory, to the letter

The back office reads the file to know which texts to replace. The contract is strict:

- a single `<script type="application/json" id="sada-screens">` tag at the end of the document;
- it contains a valid JSON object;
- the keys are **exactly** `avant`, `priere`, `entracte`, `termine`, `janaza`, `dars_fin` (the six,
  even if some texts are empty);
- the sub-keys are **exactly** `eyebrow`, `title`, `sub`, `foot`;
- the values are the default texts; an empty value `""` means “no line”;
- the names of the elements that carry these texts are given by the `"id"` key, at the same level,
  for each screen: `{"id": {"eyebrow": "…#eyebrow", "title": "…#title", …}, "texts": {…}}`.

Exact shape expected:

```html
<script type="application/json" id="sada-screens">
{
  "avant": {
    "id":    {"eyebrow": "#av-eyebrow", "title": "#av-title", "sub": "#av-sub", "foot": "#av-foot"},
    "texts": {"eyebrow": "Starting shortly", "title": "Example Mosque", "sub": "Starts at {heure}",
              "foot": "Thank you for waiting, the broadcast starts very soon."}
  },
  "priere": {"id": {…}, "texts": {…}},
  "entracte": {"id": {…}, "texts": {…}},
  "termine": {"id": {…}, "texts": {…}},
  "janaza": {"id": {…}, "texts": {…}},
  "dars_fin": {"id": {…}, "texts": {…}}
}
</script>
```

The `id` paths are simple CSS selectors that designate the element to fill (e.g. `#av-foot`). A field
with no matching element: put an empty string and leave the text absent from the screen.

## 6. How a screen is shown

The document shows **one single screen at a time**. A `data-ecran` field on each screen, with values
identical to the keys above. The visible screen is the one whose `data-actif` attribute is present:

```html
<section class="ecran" data-ecran="avant" data-actif>…</section>
<section class="ecran" data-ecran="priere">…</section>
```

In CSS: `.ecran { display: none } .ecran[data-actif] { display: flex }`. To look at another screen
during design, the mosque only has to move `data-actif`. In the back office preview, the file is shown
as it is: it is the screen marked `data-actif` that appears.

This document serves for **design and reference**: it shows the visual intention and carries the
texts. It is not the one broadcast. On air, Sadā draws its own screens with the imported
texts — colours, font and HTML composition are not reused.

## 7. What you must ask before writing

Ask these questions in one go, then wait for the answers. If the mosque does not answer or answers
“do as you like”, take the default values indicated.

1. Mosque name as it must appear (`{mosquee}`).
2. Colours: a dark dominant colour and an accent colour, or “ours” (deep green `#12301F` and matte
   gold `#C8A96A`).
3. Logo: does the mosque have one? If so, ask it to describe it, or leave an empty circle commented
   and use an emblem drawn in CSS (circles, a simple arabesque). An image is not loaded in the
   preview: do not promise a logo that will not show.
4. One welcome line to show on the before-the-live screen? (name, number, website, time)
5. Language: French only, or one line in Arabic as well? If Arabic, the text is to be supplied by the
   mosque, you do not write it.
6. Does it want the time visible somewhere, or the mosque number in the footer?

## 8. What you return

A single code block containing the whole file, ready to save as `ecrans.html`, followed by two or
three sentences: what you chose, what has to be replaced (logo, example texts), and how to check the
screen (open the file in a browser).

Then, in Sadā: Settings → Screens → **“Import a template”**, choose that file. The import
checks it, keeps it as a reference and takes the texts of the `#sada-screens` block; it reports what
it ignores and why (for example `avant.title`, which the engine does not know). Remind the mosque:
your file is what goes on air, not a recomposition of it.

Before returning, read these checks again — they are eliminatory:

- [ ] the six screen keys are present in the JSON, with the four sub-keys;
- [ ] the JSON is valid (no trailing comma, double quotes everywhere);
- [ ] each `id` of the JSON matches an element really present in the HTML;
- [ ] no emoji, no remote font or image, no `http://`;
- [ ] all the important content fits in the safe area;
- [ ] the file shows a correct screen at 1280 × 720 and does not overflow on the edges;
- [ ] an empty text does not leave a gaping hole: use the `.vide { display: none }` logic.

## 9. Example of the expected structure

This is not to be copied as it is: it is the minimal skeleton that the back office knows how to read.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Screens — live</title>
<style>
  :root { --fond: #12301F; --accent: #C8A96A; --texte: #F4F0E6; }
  * { margin: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; background: var(--fond); color: var(--texte); }
  body { font-family: Georgia, "Times New Roman", serif; }
  .ecran { position: absolute; inset: 0; display: none; flex-direction: column;
           align-items: center; justify-content: center; gap: 2vh;
           padding: 8vh 8vw; text-align: center; }
  .ecran[data-actif] { display: flex; }
  .eyebrow { letter-spacing: .35em; text-transform: uppercase; font-size: 2.4vh; color: var(--accent); }
  .title { font-size: 7.5vh; line-height: 1.15; }
  .sub { font-size: 3.4vh; opacity: .92; }
  .foot { position: absolute; bottom: 8vh; font-size: 2.6vh; opacity: .82; }
  .vide { display: none; }
  .filet { width: 12vw; height: 1px; background: var(--accent); opacity: .6; }
</style>
</head>
<body>

<section class="ecran" data-ecran="avant" data-actif>
  <p class="eyebrow" id="av-eyebrow">Starting shortly</p>
  <h1 class="title" id="av-title">Example Mosque</h1>
  <div class="filet"></div>
  <p class="sub" id="av-sub">Starts at {heure}</p>
  <p class="foot" id="av-foot">Thank you for waiting, the broadcast starts very soon.</p>
</section>

<section class="ecran" data-ecran="priere">
  <p class="eyebrow" id="pr-eyebrow">Prayer in progress</p>
  <h1 class="title" id="pr-title"></h1>
  <p class="sub" id="pr-sub">The prayer is not broadcast.</p>
  <p class="foot" id="pr-foot">The broadcast will resume for the next khutba, in sha Allah.</p>
</section>

<!-- … entracte, termine, janaza, dars_fin on the same model … -->

<script type="application/json" id="sada-screens">
{
  "avant": {
    "id": {"eyebrow": "#av-eyebrow", "title": "#av-title", "sub": "#av-sub", "foot": "#av-foot"},
    "texts": {"eyebrow": "Starting shortly", "title": "Example Mosque", "sub": "Starts at {heure}",
              "foot": "Thank you for waiting, the broadcast starts very soon."}
  },
  "priere": {
    "id": {"eyebrow": "#pr-eyebrow", "title": "#pr-title", "sub": "#pr-sub", "foot": "#pr-foot"},
    "texts": {"eyebrow": "Prayer in progress", "title": "", "sub": "The prayer is not broadcast.",
              "foot": "The broadcast will resume for the next khutba, in sha Allah."}
  }
}
</script>
<!-- the four other screens follow the same shape: the returned file must declare all six -->
</body>
</html>
```
