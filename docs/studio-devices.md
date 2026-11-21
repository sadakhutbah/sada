# Studio devices: pairing, schedule, one publisher per mosque

A PC in a mosque runs the Sada Studio application all the time. It is **paired once**, then it asks the
server what to do every 30 seconds and **only streams while a live is ordered**. The server is the source
of truth for the schedule and for the order; the device has a single point of contact (the heartbeat).

Complements [studio-whip.md](studio-whip.md) (short-lived token, WHIP proxy).

## Decisions

* **Stream only during a live, not continuously.** A permanent stream would hold the relay path all day
  (so OBS or the control room could never take over without a fight), cost bandwidth and CPU on the PC for
  nothing, and make the recording boundaries depend on silence detection. Instead the server orders `live`
  from *start − margin* (default 120 s, per mosque, 30–900 s) to the slot's end; the relay and the recording
  follow the schedule (the orchestrator plans the same slots). A slot overrunning its end is the one risk:
  keep the durations generous (`khutba_min`, `dars_min`, or `max_min` of the programme entry).
* **Nothing is entered twice.** Slots come from what Sada already has: the **Friday khutba** (jumua times of
  the prayer settings, Fridays only, like the orchestrator) and the **programme** of dars, conferences and
  tarawih (`/dars` page; one-off or weekly, fixed time or relative to a prayer). No new schedule table.
* **Time zone.** Wall-clock times are the mosque's (`mosques.timezone`, Europe/Paris by default) and are
  converted to absolute UTC instants: a weekly 20:00 dars is 19:00 UTC in winter and 18:00 UTC in summer; an
  hour skipped in March is shifted forward, a repeated hour in October takes its first occurrence; the length
  is real elapsed time. Prayer-relative entries need the day's prayer times: known for today (any source) and
  for later days with the calculated / typed sources; with Mawaqit a slot relative to a prayer is planned
  from its day (jumua times are kept for the following weeks).
* **Design.** `studio_plan` (slot sources as a Strategy, `decide` = pure state machine standby / scheduled
  live / manual live), `studio_devices` (pairing state machine, credentials), `studio_auth` (one `Actor`
  interface for a person or a device), `studio_publisher` (exclusivity, takeover), `studio_service` (the
  device's order, manual live), `studio_incidents` (checks as a Strategy, reconciliation, mails), `mailer`
  (SMTP transport as a Strategy).

## Pairing (RFC 8628 in spirit)

```
pending --approve (console)--> approved --first poll--> claimed
   '--10 minutes--> expired
```

| Call | Auth | Purpose |
|---|---|---|
| `POST /api/v1/studio/pair/start` | none, 10/min per address | `200 {device_code, user_code, expires_in: 600, interval: 5}`. `user_code` is 8 characters from a 27-letter alphabet without vowels or 0/O/1/I/L, shown `ABCD-2345`. |
| `POST /api/v1/studio/pair/poll` `{device_code}` | none, 30/min per address | `200 {status:"pending", interval, expires_in}` while waiting; `200 {status:"approved", device_id, name, mosque:{name,slug}}` once approved (**the credential is in `device_id`**, shown once); `410 {status:"expired"}`; `410 {status:"claimed"}` once the device has spoken; `404 {status:"unknown"}`. A credential delivered but never used can be fetched again (the answer may have been lost): the new secret replaces the old one. |
| `POST /api/v1/studio/devices/pair` `{user_code, name}` | session + CSRF, `live` right as administrator or manager | attaches the device to **the session's mosque**. `404 unknown_code`, `409 already_used`, `409 name_taken`, `410 expired`, `422 name_required`, `429` (10 guesses/min per account). |

Only SHA-256 hashes of the two codes and of the secret are stored. The credential has the form
`sdev_<id>.<secret>` and is sent as `Authorization: Bearer <credential>`. It opens the broadcast of its
own mosque and nothing else: the routes below, and no back-office route.

## Device routes (`Authorization: Bearer sdev_…`)

A wrong or unknown credential answers `401 {"error", "code":"invalid"}`; a **revoked** device
`401 {"code":"revoked"}`, at its very next call (a token minted before is refused by publish-auth too).

### `POST /api/v1/studio/heartbeat` (every ~30 s)

Body (all optional): `{state: "standby"|"live"|"error", version, camera: bool, microphone: bool,
bitrate_kbps, health: {auto_login: bool, update_hours: bool, sleep_disabled: bool}}`.
`health` carries the PC checks (Windows automatic sign-in, Windows Update active hours covering the slots,
sleep disabled); only an explicit `false` is a problem.

```json
{
  "server_time": "2027-03-23T18:58:05Z",
  "command": "live",                 // "standby" | "live"
  "reason_code": "slot",             // idle | slot | manual | slot_stopped | held | other_device
  "reason": "A live is planned: start broadcasting.",   // in the request language (Accept-Language)
  "current": {"source": "schedule", "slot_id": "jumua-0@20270326T1200Z", "type": "khutba",
              "title": "Khutba du vendredi", "starts_at": "…Z", "ends_at": "…Z", "starts_in_s": -12},
  "next":    {"slot_id": "…", "type": "dars", "title": "Tafsir", "starts_at": "…Z", "ends_at": "…Z", "starts_in_s": 5400},
  "slots":   [ /* the next 5 slots, same shape as `next` */ ],
  "config":  {"name": "Prayer hall PC", "mosque": {"name", "slug", "timezone"}, "heartbeat_interval_s": 30,
              "start_margin_s": 120, "token_path": "/api/v1/live/studio/token", "whip_path": "/api/v1/live/studio/whip"},
  "credentials": {"device_id": "sdev_…"}   // only when a rotation was requested: store it, use it from now on
}
```

`current.source` is `schedule` or `manual`. `current` is `null` in standby. Only one device per mosque is
ordered `live` (the manual live's device, else the holder of the stream, else the oldest online): the others
get `standby` / `other_device`. A device displaced by an administrator gets `standby` / `held` for 15 minutes.

### Order rules (state machine)

* From *start − margin* to the end of a slot: `live` (`source: schedule`).
* A **manual live** (below) is `live` until it is stopped. **A scheduled slot never cuts it**, whatever the time.
* A **manual stop during a slot stops that slot**: `standby` / `slot_stopped`, **no automatic restart** before
  the next slot (which `next` announces). A manual live can still be started afterwards.
* A manual live whose device has been silent for 15 minutes is considered over (journal: `silence`).

### Manual mode

* `POST /api/v1/studio/live/start` `{type: "khutba"|"dars"|"conference", title}` → `200` with the same body as
  a heartbeat answer (`command: "live"`, `current.source: "manual"`). Idempotent for the same device.
  `409 {error, code:"live_active", source:{kind,label,since}}` when a stream is already active (text:
  “A live is already running from the device “PC”.”), `409 held`, `422 bad_type`.
* `POST /api/v1/studio/live/stop` → `200 {stopped: ["manual"|"slot", …], …order}`. Ends the manual live and
  the slot running now; idempotent; cuts the device's stream on the relay if it is still there.
* `POST /api/v1/studio/live/announce` `{type, title}` → type and title of the live in progress (manual, or
  the running slot: the announced values replace the planned ones). `409 no_live` when nothing is live.
  The orchestrator reads them with `GET /api/v1/internal/studio-live` (agent token) to name the replay.

### Token and WHIP

`POST /api/v1/live/studio/token`, `POST|PATCH|DELETE /api/v1/live/studio/whip[/{session}]` accept the device
credential as a bearer **in addition to** the session cookie. For a device, the token is refused with
`409 {code:"not_ordered"}` unless it is ordered `live`, and with `409 {code:"live_active"}` when another source
holds the stream. The WHIP start/end are journaled (`studio.whip.debut|fin`, with the device name).

## Exclusivity: one publisher per mosque

* `deploy/helm/sada/files/mediamtx.yml`: `overridePublisher: no` on `live/*` (MediaMTX replaced the
  current publisher by default, so a second source could silently cut a live). Chart version 0.1.5.
* `/internal/publish-auth` asks the relay who publishes (`GET /v3/paths/get`): a new publication (device,
  OBS key or control room) is refused with `401 {error: "A live is already running from <source>.",
  code: "live_active", source}` and journaled (`studio.publication.refusee`). The same holder reconnecting
  replaces its own stale session (kicked through the relay API). If the relay API does not answer, nothing is
  claimed and the relay's own `overridePublisher: no` is the backstop.
* The console (Devices page and control room) shows “A live is already running from <source>” and the token /
  WHIP routes answer `409 live_active`.
* **Takeover**: `POST /api/v1/live/studio/takeover` `{confirm: true}` (session, CSRF, administrator of the
  mosque). Kicks the current publisher (`POST /v3/<kind>/kick/<id>` on the relay API), clears the claim,
  puts a displaced device on hold for 15 minutes, journals `studio.reprise` (who, whom). `422` without
  confirmation, `409` when nothing runs, `403` for a manager.
* **Revocation** also kicks the device's live in progress (best effort) and ends its manual live.

## Console routes (session + CSRF, `live` right as administrator or manager; always the session's mosque)

`GET /studio/devices` (devices, open incidents, publisher, order, next slots, settings, mail state),
`PATCH /studio/devices/{id}` `{name}`, `POST /studio/devices/{id}/revoke`, `POST /studio/devices/{id}/rotate`
(the device receives a new credential with its next heartbeat; the old one works for one hour or until the new
one is used), `GET /studio/schedule`, `GET /studio/incidents`, `GET|PUT /studio/settings` (PUT: administrator;
`start_margin_s`, `offline_alert_min`, `khutba_min`, `dars_min`, `preflight_min`),
`PUT /studio/alerts/preferences` `{email?, subscribed?}` (the account's own). Another mosque's device or
incident is a `404`. Page: **Appareils** (`/appareils`, next to the control room).

## Sanity checks and alert e-mails

Computed on the server from the heartbeat and the schedule, by a light periodic job (every 60 s,
`SADA_STUDIO_WATCH_S`, `0` = off): a few indexed queries per mosque that has a device, one relay call only
while a scheduled live runs. Mosques without a device are not touched.

| Incident | Trigger |
|---|---|
| `offline` | no signal for `offline_alert_min` (default 10) minutes |
| `camera`, `microphone` | the online device reports it missing |
| `health_auto_login`, `health_update_hours`, `health_sleep` | `health.<field>` is `false` |
| `preflight` | a slot starts within `preflight_min` (30) minutes and no device is online, or the online ones lack camera or microphone |
| `start_failed` | a scheduled live is ordered, 3 minutes past its start, and the relay receives nothing (or the device says `error`) |

An incident opens once (unique while open), is shown on the Devices page and journaled
(`studio.incident.ouvert|resolu`). Mails (French, with what to do and a link to `/appareils`): to the
accounts of **that mosque** with the `live` right as administrator or manager, an address, not unsubscribed.
**One mail per problem** (after a 2-minute grace, none for `preflight` / `start_failed`), at most 2 reminders 6 h
apart, simultaneous problems grouped in one mail per recipient, one “resolved” mail when mailed problems are
gone (none for a problem nobody heard of). Unsubscription per account: the link in the mail (also
`List-Unsubscribe`), or the Devices page; the address is set there too. Journal: `studio.mail.envoye|resolu|echec|aucun_destinataire`.

### Sending e-mail

Sada had no e-mail before: `sada/mailer.py` adds SMTP, configured by environment (nothing is stored):
`SADA_SMTP_HOST` (empty = dry run, messages go to the log), `SADA_SMTP_PORT` (587), `SADA_SMTP_SECURITY`
(`starttls` | `ssl` | `none`), `SADA_SMTP_USER`, `SADA_SMTP_PASSWORD`, `SADA_MAIL_FROM`, `SADA_SMTP_TIMEOUT`.
Links use the instance public address (`data/instance.json`) or `SADA_PUBLIC_URL`. The Helm chart does not
inject these variables yet (to be added in the deployment values, secret for the password).

## Journal (audit) actions

`studio.appareil.appaire|renomme|revoque|rotation.demandee|rotation`, `studio.appairage.refuse`,
`studio.direct.manuel.debut|fin`, `studio.direct.annonce`, `studio.creneau.arret`, `studio.reprise`,
`studio.publication.refusee`, `studio.reglages`, `studio.alertes.preferences|desabonnement`,
`studio.incident.ouvert|resolu`, `studio.mail.*`, plus `studio.jeton` and `studio.whip.debut|fin`.
