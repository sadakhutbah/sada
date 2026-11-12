# The orchestrator and the tokens

Two programs run side by side on the same machine. This document explains what each one does, why
they talk to each other, and what the famous **internal token** is for — the `internal.token` file
nobody remembers.

---

## 1. The two programs

| Program | What it does | Who sees it |
| --- | --- | --- |
| **The back office** | the interface in the browser: settings, khutbas, clips, log, screens | the administrator, with their account and password |
| **The orchestrator** | the live itself: it opens the camera, encodes, pushes to the platforms, listens to the voice, cuts, posts the recording | nobody: it has no screen |

The back office is a web server. The orchestrator is a command-line program. They share **no
memory**, and no database either: the orchestrator never opens the back office's SQLite file.

## 2. Why the orchestrator needs to talk to it

Some settings entered in the browser cannot be known before the live starts: the address where an
end-of-live notification is posted, the messages to send, the screen texts, the stream destinations.
The orchestrator therefore asks for them **at startup**, through a local HTTP call, from the only
source that has authority: the back office.

In practice, at startup, the orchestrator queries:

- `/api/v1/internal/notification-settings` — where to warn when a khutba is published;
- `/api/v1/internal/live-targets` — the live destinations;
- `/api/v1/internal/screens` — the texts of the full screens;
- `/api/v1/internal/live-input` — the camera or the OBS push to read;
- `/api/v1/internal/storage` — the optional upload of recordings to a bucket.

If the back office does not answer, the orchestrator does not stop: it carries on with what it has
in its configuration file, and notifications stay blank. A closed back office never breaks a live.

## 3. Two tokens, not to be confused

The word “token” appears twice in the project, and they go **in opposite directions**:

| Token | Who sends it | To whom | Where it is entered |
| --- | --- | --- | --- |
| **internal token** (`internal.token`) | the orchestrator | the back office | nowhere: local file, created on its own |
| **control token** (`orchestrator_token`) | the back office | the orchestrator | Settings → Mosque, and `--api-token` by hand |

- The **internal token** protects the addresses the orchestrator queries to *read* its settings.
  It always exists, it is automatic, there is nothing for you to do.
- The **control token** protects the addresses the back office calls to *drive* the live: start,
  stop, extend, change scene. It is **optional**: left empty, the orchestrator accepts commands from
  any program on the machine. Once set, it requires the `X-Token` header, and the **same value**
  must be given on both sides — the back office field and the `--api-token` option when the
  orchestrator is launched.

If the dashboard buttons answer “orchestrator unreachable” while it is running, it is almost always
this control token: set on one side, different or missing on the other. Clear the field and restart
both to get back to a simple state.

## 4. The problem the internal token solves

These addresses are on `127.0.0.1`, but “local” does not mean “private”. On the same machine, any
program runs with the same rights: a tab open on a malicious site can call
`http://127.0.0.1:8801/api/v1/internal/screens` and read the mosque settings; another installed
piece of software can do the same. An administrator session does not work here: a program has no
cookies, no sign-in page, no CSRF.

Hence the token: **a shared local secret**, which only one machine — and in practice only one user —
can read.

- it is created at the first need, by whichever of the two programs starts first;
- it lives in `data/internal.token`, with `0600` permissions (readable by its owner only);
- it is drawn at random (`secrets.token_urlsafe`), 43 characters, never displayed in an interface;
- it is sent in an `X-Internal-Token` header, compared in constant time (no leak through the
  response time);
- without it, these addresses answer `403 jeton interne requis`.

It is a **lock on an inner door**, not a safe lock: it stops another program on the machine from
helping itself to the settings. It replaces neither the administrator's password nor network
encryption.

## 5. What not to do

- **Do not publish this file.** It does not go into the repository (the `data/` folder is ignored)
  and it is not sent by messaging.
- **Do not put it in a back office setting.** It cannot be changed from the interface, and that is
  deliberate: an interface does not hand out a secret.
- **Do not copy it to another machine.** If the orchestrator runs somewhere other than the back
  office, you then need a real private network (VPN, Tailscale) and not a copied token.

If you delete it, nothing breaks: the file is recreated at the next startup of both programs, with
a new value. Restart them both, otherwise one will present the old token and be refused until its
own restart.

## 6. Getting started, in order

1. **Fill in the settings** in the back office: mosque, camera or OBS push, destinations,
   notifications, screen texts. This is what the orchestrator will read.
2. **Leave the back office running**: move it to a dedicated terminal, or run it as a service.
3. **Start the orchestrator** in a second terminal: it asks the back office for its settings and
   shows in its log what it received, or the reason why it received nothing.
4. **Check** that the token exists and is readable by you only:

```bash
ls -l data/internal.token      # -rw------- expected
```

## 7. Frequently asked questions

**Do I have to enter the token somewhere?**
No. Both programs read it from the same file. It is never typed by hand.

**The interface asks me for a token, is that serious?**
Two cases. On an `/internal/...` address, it is a direct call from a browser: not an intended path,
settings go through `/api/v1/settings`. On the dashboard buttons, see section 3: it is the control
token that does not match.

**Can I do without it?**
Yes, by running both programs in the same process — but then the back office falls with the live,
and an interface error cuts the broadcast. Separation costs this token; it is worth the price.

**What if I change machine?**
You take `data/` with you (database and token), or you let both programs recreate a fresh token on
the new machine.
