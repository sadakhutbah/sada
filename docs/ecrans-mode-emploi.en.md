# Screen manual

This document is for **the person who administers Minbar Live in a mosque**. No technical
vocabulary, no prerequisites: if you can fill in a form, you can do this.

---

## 1. What these screens are for

During a live, the camera does not always show something useful: before the khutba starts, while
people are settling in, between two sequences, when the hall empties. A full screen — an image with
text, in place of the video — avoids broadcasting an empty hall or people who do not want to be
filmed.

In Minbar Live, this is called a **screen**. There are six, each for a precise moment of the live.
The engine shows them automatically; you have nothing to trigger.

You can change **the texts** of these screens. You do not change their layout: we take care of that
so it stays readable on a television and on a phone.

---

## 2. The six moments, and what to put on each

| Screen | When it appears | What usually goes there |
| --- | --- | --- |
| **Before the live** | before the start, during the wait | the mosque name, the start time, a word of patience |
| **Prayer** | during the prayer, whose picture is never broadcast | “the prayer is not broadcast”, and when it resumes |
| **Break** | between two sequences | a word to say that it continues |
| **End** | at the end of the khutba | a thank you, the next appointment |
| **Janaza** | for a funeral prayer | the name of the deceased *if the family agrees*, otherwise nothing |
| **End of a class** | at the end of a dars | a thank you, the date of the next class |

A screen that does not concern you is left empty: it will not appear, or will keep its original
text. Nothing is compulsory.

**A screen is not a notice board.** It is seen from a distance, sometimes on a phone held in the
hand, for five seconds. Three lines are enough; beyond that, nobody reads.

---

## 3. Filling in a screen, step by step

1. Menu on the left → **Settings**.
2. At the top, the **Screens** tab.
3. A row of sub-tabs: *Before the live*, *Prayer*, *Break*, *End*, *Janaza*, *End of a class*.
   Click the one you want to change.
4. Four fields appear, from the smallest at the top to the largest at the bottom. On the right, the
   preview shows the screen as it will go on air — it updates while you type.
5. Type your text. The grey text in an empty field is what is shown **by default**: if you do not
   want to change anything, do not touch it.
6. When you have finished several screens, click **Save settings** at the bottom.

To **go back to the original text** of a field: clear it completely, then save. An empty field does
not mean “nothing on screen”: it means “put back what was written before”.

---

## 4. The braces: `{heure}`, `{date}`, `{jour}`…

Some texts contain braces. These are not typing mistakes: Minbar Live replaces them at the moment
of the live.

- `{heure}` → the session time, for example “Starts at **{heure}**” becomes “Starts at **19:30**”.
- `{date}` → today's date, written out in full.
- `{jour}` → the weekday of the next appointment.
- `{cours}` → the title of the class that is about to start or has just ended.
- `{titre}` → the official title of the session (khutba, dars, lecture).
- `{mosquee}` → the name of your mosque, as recorded in Settings → Mosque.

Watch out for a trap: if the information does not exist that day, the brace simply disappears. A
sentence built around it can therefore become lopsided. The typical case: “Starts at {heure}” when
no time is set gives “Starts at” and nothing after it. Put the brace in a sentence that stands on
its own: “The broadcast starts at {heure}.”

---

## 5. What these screens cannot do

- **Change the colours, the font, the place of the logo.** That is the layout, and it is common to
  all mosques.
- **Show an image, a photo, an announcement.** These screens are text.
- **Show a verse or a hadith you have not written yourself.** No religious text is supplied by
  default: what you write is what appears, and it is for you to check it.
- **Apply retrospectively.** Texts are read **when the live starts**. If you change them during a
  live in progress, they will change for the next live, or after the live is restarted. This is not
  a bug.

---

## 6. A look entirely of your own

The six base screens share one design, the Minbar Live one. A mosque that wants **its own look** —
its colours, its font, its logo, its composition — can have it made by a chat assistant.

The principle: you give the assistant a written instruction, it returns an HTML file, and that file
becomes the visual reference for your screens — and the source of their texts.

1. Settings → Screens: the page shows an **address** (a link ending in
   `agent-gabarit-ecrans.md`). Copy it with the button provided.
2. Open ChatGPT, Claude, Gemini, or any assistant: paste this address into the chat and ask it to
   follow the instructions. If it cannot open a link, open the address in your browser, select all
   the text on the page, and paste it.
3. The assistant will ask you a few questions (mosque name, colours, logo, language, welcome line).
   Answer them. If you answer “do as you like”, it will make neutral choices.
4. It returns a file named `ecrans.html`. Retrieve it.
5. Still in Settings → Screens, click **“Import a template”** and choose that file.
   The back office checks it: if it does not comply, it explains why and changes nothing. If it
   complies, it keeps it as the mosque's reference, shows it in the preview, and takes the texts of
   the `#minbar-screens` block to fill the fields of the six screens. These texts are saved straight
   away; read them again and correct them if need be, like any other text.

This file remains your property: it contains only text and colours, it runs without internet, and it
does nothing but display.

What you must know, without ambiguity: **the HTML file is not what goes on air.** Minbar Live goes
on composing the six screens with its own engine, reusing the imported texts. The imported file
serves as a visual reference — to recover your intention, compare, and supply the texts — not as a
broadcast rendering. The colours, the font and the composition of your template are therefore not
carried over on air. Broadcasting an HTML rendering in place of the engine is another job, which
does not exist today: do not count on it.

---

## 7. Checking that your text is right

1. Look at the preview on the right: it is exactly what the viewer will see.
2. Check that no line overflows — a text that is too long is shrunk or cut, and the preview shows
   it.
3. Ask yourself whether you would understand that sentence **on a phone, at one metre, without sound**.
   If not, shorten it.
4. After the first live where you changed them, look at the file in the replay: it is the only
   judge.

---

## 8. A few text examples, to adapt

- Before a khutba: *(eyebrow)* `Friday` · *(title)* `Khotbat al-Jumu'a` · *(subtitle)*
  `Starts at {heure}` · *(footer)* `Please switch off your phone.`
- Before a class: *(eyebrow)* `Evening dars` · *(title)* `{titre du cours}` · *(subtitle)*
  `Starts at {heure}` · *(footer)* `Questions are taken at the end.`
- End of a class: *(eyebrow)* `End of the dars` · *(title)* `Jazakoum Allahou khayran` · *(footer)*
  `Next class: {jour} at {heure}.`
- Janaza: *(eyebrow)* `Salat al-Janaza` · *(title)* `Funeral prayer` · *(footer)*
  `The live will resume after the prayer.`

---

## 9. Frequently asked questions

**Is an empty field a screen without that text?**
No. An empty field restores the default text. For a line to really disappear, it has to be cleared
in the template itself, not in the form.

**My changes do not appear during the live.**
They apply when the next live starts. This is deliberate: a text must not change in the middle of a
khutba.

**I cleared everything by mistake.**
Reload the page without saving: your saved texts come back. If you have already saved, you have to
type them again.

**Can I have two sets of texts, one for Friday and one for classes?**
Not yet. The texts are common to the mosque. An improvement is possible.

**Who else can change these texts?**
The accounts that have access to Settings, that is, the mosque administrator.
