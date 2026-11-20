# Prayer times

Sadā needs prayer times to arm the live (Friday khutba, a lesson after a prayer, tarawih) and for its screens. They come from a **source**, chosen in Settings › Prayer times (or in the first-run wizard):

| Source | What you do | Public page |
|---|---|---|
| **Mawaqit** | Nothing to enter: give the mosque's Mawaqit address or identifier. | No: Sadā neither copies nor publishes the Mawaqit calendar. |
| **Calculated** | Method, coordinates, offsets. Sadā calculates every day. | Yes |
| **Typed by hand** | You type (or import as CSV) your own times. | Yes |

If Mawaqit disappears nothing stops: choose "Calculated" or "Typed by hand".

## Calculated

- **Method**: UOIF 12°/12°, Muslim World League 18°/17°, ISNA 15°/15°, Egypt, Umm al-Qura, Karachi, Morocco, Diyanet, or custom angles (Fajr, Isha; Isha can also be a delay after Maghrib).
- **Asr**: shadow ×1 (Shafi'i, Maliki, Hanbali) or ×2 (Hanafi).
- **High latitudes**: the rule applied when the sun does not reach the angle (middle of the night, one seventh of the night, angle based).
- **Coordinates**: the mosque's latitude and longitude ("My position" fills them).
- **Adjustments**: an offset in minutes per prayer, an iqama delay after the adhan, and limits ("Isha never before 19:10", "Dhuhr never before 13:00").
- **A day to correct**: in the Timetable tab, a filled box replaces the calculation for that day only; in grey, the calculation.

The preview shows the result before saving. The calculation relies on the `praytimes` library (the PrayTimes.org algorithm).

### Leaving Mawaqit without changing times

In the Calculation tab, "Deduce from Mawaqit" reads the mosque's Mawaqit calendar once, looks for the method, offsets and limits that reproduce it, and suggests them with the largest gap in minutes. Nothing from Mawaqit is kept. On the command line: `python -m sada.prayer_fit <Mawaqit address> --year 2027`.

Many mosques' Mawaqit calendars are themselves calculated (Fajr, sunrise, Dhuhr and Maghrib are found back to the minute); Asr sometimes differs by one or two minutes, and a mosque that types its times by hand is only partly reproduced: the announced gap says so.

## Typed by hand

- **Month grid**: one line per day. Enter and the arrow keys change line; pasting cells from a spreadsheet fills the grid. "Show iqama" adds the iqama columns; "Fill the iqama" sets adhan + N minutes.
- **CSV** (Import tab): columns `date, fajr, sunrise, dhuhr, asr, maghrib, isha`, then `fajr_iqama`… (`+10` = adhan + 10 min). Comma, semicolon or tab separator; dates `YYYY-MM-DD` or `DD/MM/YYYY`. Everything is checked before writing: one error and nothing is imported. "Export the year" gives a CSV the import reads back: a calculated year thus becomes a timetable to edit.

## Public page (read-only)

For a **calculated** or **manual** mosque (box "Public timetable page"):

```
GET /api/v1/public/mosques/<slug>/prayer-times               # today
GET /api/v1/public/mosques/<slug>/prayer-times?date=2027-03-21
GET /api/v1/public/mosques/<slug>/prayer-times?month=2027-03
```

No sign-in, only the times (never an address or a setting). Readable from any web page (CORS), cached for a minute, limited to 60 requests a minute per client (429 with `Retry-After`). A Mawaqit mosque answers 404 with the address of its Mawaqit page.
