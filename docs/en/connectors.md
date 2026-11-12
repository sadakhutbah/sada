# Connectors: Facebook, YouTube, Instagram

This document is for the mosque administrator. It explains what you have to obtain from each
platform, where to enter the credentials, what the publish button does, and what the connector does
not do.

---

## 1. The common rule

A connector links a clip to an account. It never publishes on its own: you need a clip **approved by
a human**, then a press on **Publish** in the clip's page. This rule is checked on the server side:
a clip that is not approved is refused, even if the screen gets it wrong.

The back office offers four destinations:

| Connector | What it does | Credentials needed |
| --- | --- | --- |
| **Local folder** | copies the clip and its record into a folder on the machine | none; active by default |
| **Facebook page** | posts the video on the mosque's page | page identifier, page token |
| **YouTube** | sends the video to the mosque's channel | client ID, client secret, refresh token |
| **Instagram** | publishes a Reel on the professional account | account identifier, token, public address of the folder |

Without credentials, a connector stays **unavailable** and no call is made to the platform. A refusal
from the platform is recorded as it is in the history: its message, not a translation.

Every attempt, successful or refused, leaves a trace in **Publishing → History**: the clip, the
connector, the date, the person and the result. A success shows the public address of the
publication; a refusal shows the platform's message. For the local connector, the result is the path
of the copied file.

## 2. Facebook

### What you need to obtain

- a **Facebook page** for the mosque, and an account that administers it;
- the **page identifier** (a number, visible in the page settings);
- a **page access token** allowed to publish videos. It is made in Meta's developer tools, from an
  account that manages the page.

A page token obtained from a long-lived user token does not expire on its own. It does fall if the
person changes their password, revokes the application or loses their role on the page. The back
office does not renew it: you will have to make another one.

**Where to enter it.** **Publishing** page, **Connectors** tab, **Facebook page** card:

| Field | What goes in it |
| --- | --- |
| Page identifier | the page number, not its web address |
| Page access token | the token, hidden after saving |

Then tick **Enable this connector** and save.

**What is published.** The video goes with the caption from the record, cut at 5,000 characters if
it is longer. The video is public as soon as it is sent. The result is the address of the
publication.

## 3. YouTube

### What you need to obtain

- the mosque's **channel**, and a Google account that can publish to it;
- a project in Google Cloud with the **YouTube Data API** enabled;
- an **OAuth client** of the desktop application type, with its ID and its secret;
- a **refresh token** obtained once with offline access. This token is what allows sending without
  asking for a sign-in every time.

The refresh token is made outside the back office, with Google's tools. If it is revoked, expired or
deleted, the upload fails with `invalid_grant` and you have to produce a new one.

**Where to enter it.** Same place, **YouTube** card:

| Field | What goes in it |
| --- | --- |
| Channel identifier | optional; acts as a marker, the token is what chooses the channel |
| OAuth client ID | the ID of the client created in Google Cloud |
| OAuth client secret | its secret, hidden after saving |
| Refresh token | the offline token, hidden after saving |
| Visibility | `public`, `unlisted` or `private`; `public` by default |
| Category | `27` (education) by default |

**What is published.** The title is built automatically: the clip title and the mosque name, then
the date, cut at 100 characters. The caption serves as description, cut at 4,900 characters. The
upload can last up to thirty minutes; if it fails, nothing is published and the message is recorded.

YouTube limits uploads: one upload costs about 1,600 units out of the 10,000 daily, that is about
six publications a day. Beyond that, the platform answers `quotaExceeded` until the next day.

## 4. Instagram

### What you need to obtain

- an Instagram **professional account** (business or creator), linked to a Facebook page;
- the **identifier of the professional account** (a number, not the username);
- an **access token** allowed to publish content on that account;
- a **public address** for the publishing folder: a web address that serves the files deposited by
  the mosque, without a password.

Instagram does not receive a file. Its API **downloads the video itself** from the public address.
The back office therefore first copies the clip into the publishing folder, then builds the address
`adresse-publique/date/nom-du-clip.mp4` and gives it to Instagram.

**Where to enter it.** Same place, **Instagram** card:

| Field | What goes in it |
| --- | --- |
| Professional account identifier | the account number, not its name |
| Access token | the token, hidden after saving |
| Public address of the folder | the address that serves the publishing folder |
| Publishing folder | the one where the clip is copied before sending; proposed by default |

If the public address does not start with `http://` or `https://`, the connector stays unavailable
and says so. This is deliberate: without a valid address, Instagram could download nothing.

**What is published.** A vertical Reel, with the caption entered, cut at 2,200 characters, shared in
the account's feed.
The platform takes a few seconds to prepare the video; the back office waits up to two minutes.
Beyond that, the upload is refused with `délai dépassé` and nothing is published.

The video must remain reachable throughout. A file served only on the local network, or behind a
sign-in page, will not be downloaded by Instagram.

## 5. Where to enter the credentials, and what the screen shows

The path is **Publishing → Connectors tab**. The **Settings → Publishing** link goes there directly:
there is no separate form in the settings.

Each card shows:

| Item | What it means |
| --- | --- |
| **Available** | the mandatory credentials are saved and the connector is enabled |
| **Unavailable** | the connector will not publish; the reason is written under the description |
| **Configure** | opens the fields and the activation box; reserved to administrators |

The reasons for unavailability are precise: for example `désactivé : identifiants absents (page_id,
page_token)`, `désactivé par l'administrateur`, or the public address that is not a URL.

A secret field is never shown again. It appears hidden; leaving it empty or sending the dots back
keeps the saved value. Entering it again replaces it.

**There is no Test button for the three networks.** Saving only writes values into the database:
that proves nothing. The only check is a publication, and it is real — it appears online. Do it on a
clip you accept to see published; the result is in the history. The local folder is checked, for its
part, by looking at the copied files.

## 6. What the connectors do not do

- **No scheduling.** A publication goes at the moment of the press, not at a chosen time.
- **No automatic republishing.** Nothing goes again on its own after a failure. A clip already
  published can be sent again if you press again: the history keeps both attempts, and the platform
  will have two videos. Do not do it without reason.
- **No change or deletion** on the platform. Correcting a published caption is done in Facebook,
  YouTube or Instagram, not here.
- **No editing of the video.** The clip file goes as it is; no resizing, no change of format.
- **No statistics.** Views, comments and subscribers do not come back into the back office.
- **No renewal of tokens.** Facebook and Instagram do not reconnect on their own; YouTube renews its
  access token from the refresh token, but does not make that token.
- **No multiple accounts.** One set of credentials per mosque and per network: the last one saved
  wins.

## 7. Common errors

The platform's message is copied into the history. The cases that come back:

| Message | Probable cause | What to do |
| --- | --- | --- |
| `HTTP 400 — … Invalid OAuth access token` | Facebook or Instagram token expired, revoked or copied wrongly | make a new token and enter it |
| `HTTP 403 — … (#200) … permissions` | the account has no right to publish on the page or account | check the role on the page and the rights asked of the token |
| `HTTP 400 — … (#100) … param` | wrong page or account identifier | take the exact number, not the name |
| `jeton Google : HTTP 400 — invalid_grant` | refresh token expired or revoked | produce a new one with offline access |
| `HTTP 403 — quotaExceeded` | the day's YouTube quota is used up | wait until the next day, or ask for a quota increase |
| `HTTP 400 — Media download failed` | Instagram could not read the public address | check the address, the folder and the file name |
| `Instagram n'a pas fini de préparer la vidéo (délai dépassé)` | preparation too long or file refused | try again; if it repeats, check the file |
| `désactivé : identifiants absents (…)` | a mandatory field is empty | fill in the named fields and save |
| `clip non validé : publication refusée` | the clip is not at the Approved status, or has to be regenerated | approve the clip before publishing |
| `fichier du clip introuvable` | the file was moved or deleted | regenerate the clip, then approve it |

An `erreur : …` error followed by technical text comes from a network incident or an unreadable
file. Trying again is harmless: a failed attempt publishes nothing.

## 8. Frequently asked questions

**Do I need a professional account for Facebook and YouTube?**
No. Facebook asks for a page and a page token; YouTube asks for a channel and an OAuth client. Only
Instagram requires a professional account, linked to a Facebook page.

**Can I publish the same clip on the three networks?**
Yes, one publication per network, from the same page. Each press is independent: a refusal on
Instagram does not stop Facebook or YouTube from succeeding.

**Why is the connector unavailable when I have saved?**
Three causes: a mandatory field is empty, the **Enable this connector** box is not ticked, or
Instagram's public address is not a web address. The reason is written under the description.

**Is the caption the same everywhere?**
Yes, up to the cut: 5,000 characters on Facebook, 4,900 on YouTube, 2,200 on Instagram. The proposed
text reuses the clip title, the date, the khutba and the verses quoted; you can change it before
each publication.
