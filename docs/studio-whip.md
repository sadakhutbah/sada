# Studio publishing (WHIP) through the back office

A signed-in person with the `live` right can publish to their mosque's relay path `live/<slug>` from a
browser or a native client, without the permanent stream key.

* `POST /api/v1/live/studio/token` (session + CSRF): returns `{token, expires_in, path}`. The token is
  signed, bound to the user and the session's mosque, and lives `SADA_STUDIO_TOKEN_TTL` seconds
  (default 120, clamped to 30..600). Refused (409) when the mosque input is not `push`.
* `/internal/publish-auth` accepts it for `action=publish`, `protocol=webrtc`, on `live/<slug>` of the
  token's mosque only (the relay passes the bearer as `token`). The permanent key still works as before.
* `POST /api/v1/live/studio/whip` (body: SDP offer), `PATCH` (ICE, `application/trickle-ice-sdpfrag`) and
  `DELETE` on `/api/v1/live/studio/whip/{session}`: the back office relays the signalling to the relay's
  WebRTC port with a fresh token per call. Start and end are written to the journal
  (`studio.whip.debut`, `studio.whip.fin`, with user, mosque, IP and relay session).

Secret: `SADA_STUDIO_TOKEN_SECRET`. Unset, it is derived from the local internal token (fine for a single
back-office process). Set it, identical on every replica, as soon as several replicas verify tokens.
