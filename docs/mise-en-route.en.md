# Getting started

This document is for the mosque administrator. It describes the first sign-in to the back office,
the four-screen setup visit, connecting the camera or OBS, the live destinations, and what happens on
the first live.

---

## 1. Before you start: the account, the mosque, the time zone

The installation has already created the mosque and your administrator account. You should have
received:

| What you need | What it is for |
| --- | --- |
| The address of the back office | opening the interface in a browser, from a computer or a phone on the network |
| Your username and password | signing in; the administrator role opens every setting |
| The exact name of the mosque | it appears on the screens and in the published captions |
| The official name of the time zone | for example `Europe/Paris`; it sets the time of the screens and of the schedule |
| The RTSP address of the camera, or a computer with OBS | feeding the picture of the live |
| The RTMP addresses of the networks | one per destination: Facebook page, YouTube channel, other service |

Two programs run on the machine: the back office, which shows this interface, and the orchestrator,
which runs the live. The back office can stay open without a live; the orchestrator has no screen.
Getting started only concerns the settings the orchestrator reads at its startup.

The setup visit does not ask for the mosque name: it was entered at installation. Check it first of
all, in **Settings → Mosque**.

| Field | What it changes |
| --- | --- |
| **Mosque name** | the full screens, the captions, the titles sent to the networks |
| **Time zone** | the time shown on the screens, the schedule times and the log dates |
| **Clip title** | the first line of the clips and of the captions proposed for publication |
| **Mawaqit address** | optional; it gives the prayer times of the mosque |
| **Public address** | the address through which the mosque opens this back office, used in documentation links |

The time zone must be the **official name** of the zone, not an offset. `Europe/Paris` is proposed by
default; an unknown name is refused on saving. A wrong time zone does not cut the live, but it shifts
the screens and the schedule by an hour or more.

Click **Save settings** at the bottom of the page. The page reports unsaved changes; as long as they
are not saved, the orchestrator will not see them.

## 2. The visit in four screens

On an administrator's first sign-in, the back office opens the setup. It does not appear again once
finished or skipped. Four screens, one of them optional:

| Screen | What you do | What is saved when you leave the screen |
| --- | --- | --- |
| **1. Video input** | you choose the mosque camera or the push from a computer | the mode and the address read by the orchestrator |
| **2. Destinations** | you add the RTMP addresses of the networks | the whole list, with the active boxes |
| **3. Replays** | optional: you connect a storage bucket | the bucket, only if you filled it in |
| **4. Done** | you read the summary again | nothing: the setup is marked as done |

The **Continue** button saves the current screen before moving to the next one. If a value is
refused, the message appears in red and you stay on the screen: nothing is lost, correct the faulty
field.

The **Skip the setup** link ends the visit immediately, without saving the current screen. The same
settings remain accessible afterwards in **Settings → Live** and **Settings → Replays**; the visit is
not the only path.

## 3. Connecting the camera

Choose **Mosque camera** if the camera already streams its picture on the network. The field expects
an address that starts with `rtsp://` or `rtsps://`, without a space, with a named server.

An address is proposed by default: replace it with the one of your camera. The **Test** button
queries the stream for a few seconds and shows what it receives:

| What the test shows | What it means |
| --- | --- |
| `1920×1080 · 25 i/s · son présent` | picture and sound arrive: the input is good |
| `… · sans son` | the picture arrives, but the voice is not in the stream: check the camera and its microphone |
| `Flux injoignable : …` | the address does not answer from the back office server, or the stream is already taken |
| `Aucune image reçue dans le délai imparti` | the server does not answer fast enough, or the camera limits connections |
| `Le flux répond mais ne contient pas d'image` | the stream exists, but it carries only sound or data |

The test changes nothing. It starts from the server that hosts the back office: a camera on another
network can therefore be unreachable for the test while it works for the orchestrator, or the
opposite. In case of doubt, run the test again before concluding.

Once the test is satisfactory, click **Continue**: the address is saved and will be read at the next
start of the live.

## 4. Connecting OBS (push from a computer)

Choose **Pushed from a computer** if the picture is made in OBS. The screen shows an address to copy,
with a **Copy** button; it looks like
`rtmp://<adresse-du-serveur>:1935/live/<nom-de-la-mosquee>`. Do not change it by hand.

| In OBS | Value |
| --- | --- |
| Service | **Custom** |
| Server | the address shown by the back office |
| Stream key | empty |

The relay that receives the push must run before the live; it collects OBS's picture and hands it to
the orchestrator. Without it, OBS connects to nothing and the live has no picture.

The proposed address reuses the name through which you opened the back office. If you opened it from
the machine itself, the address will only be usable by OBS on that machine. To send from another
computer, open the back office through its network address before copying the address.

In push mode, the **Test** button reads the relay's copy, not the address given to OBS. It can
therefore show nothing as long as OBS is not pushing: the test fails with `Flux injoignable`. Start
OBS, then run the test again to see the resolution and the sound. The real check remains the first
live.

## 5. The live destinations

Screen 2 lists the addresses the stream is sent to. The same stream goes to all active destinations,
in the order of the list.

| Destination | Where to take the RTMP address |
| --- | --- |
| Facebook page | in the page's live tool, the stream key and the server address |
| YouTube channel | in the channel's live tool, the server address and the key |
| Other service | from the provider: an RTMP address and its key |

Rules of the screen:

- **Six destinations at most.** Beyond that, saving refuses the list.
- **A short name** per destination, 60 characters at most; without a name, the server name is used.
- The address must start with `rtmp://` or `rtmps://`, without a space, and contain a key: the last
  segment of the address.
- The key is **never shown again** after saving. The screen shows dots in its place; send the dots
  back as they are to keep it.
- The same address twice is refused. An empty address is refused.
- Without an active destination, the live is simply broadcast nowhere.

In the visit, the destinations are saved in one go, on the click on **Continue**. They are read at
the next start of the live; nothing changes during a live in progress.

## 6. Replays, as an option

The third screen offers to send the recordings of the live to a storage bucket. The box is
**unticked by default**: without it, everything stays on the mosque's machine. It is the simplest
choice and it prevents nothing.

If you wish, fill in the service, the bucket, the region, the access key and the secret key, then
tick the box. The **Test the write** button deposits a small file then removes it: it says whether
the bucket really accepts uploads. You can also leave the screen empty and click **Continue**;
everything is set later in **Settings → Replays**.

The detail of the fields and of the errors is in the document **Recordings and S3 buckets**.

## 7. Checking that everything is ready, then the first live

Before leaving the setup, check these four points:

| Point | What you should see |
| --- | --- |
| Video input | a chosen mode, a saved address, a test that shows a picture and sound |
| Destinations | at least one active destination, with a complete address |
| Time zone | the official name of the mosque's zone, saved in Settings → Mosque |
| Bucket (optional) | the box ticked and the test `Écriture acceptée` |

Screen 4 sums up this information in two lines: the input and the number of active destinations,
then the bucket if it is set. **Go to the dashboard** ends the visit.

On the first live:

1. The orchestrator starts and asks the back office for its settings: video input, destinations,
   screen texts, screen template, bucket.
1a. If the mosque imported a template, the screens shown are **that file**, rendered by the browser
   shipped with the application. One command installs it on the broadcast machine:
   `python -m minbar.backoffice fetch-engine` (about 280 MB, no administrator rights). Without it,
   the engine draws its own screens: nothing stops, the result is simply less faithful to the file.
2. If the back office is closed, it carries on with its own configuration and notifications stay
   blank. A closed back office never breaks a live.
3. The full screens appear before the khutba, then the picture goes to all active destinations at the
   same time.
4. The live is recorded on the machine.
5. On stopping, the recordings go in the background to the bucket, if you enabled it. The
   post-live processing then produces the transcript and the clips; the back office imports them
   automatically, usually within the minute.

The clips arrive with the status **To review**. Nothing is published on the networks without a human
action: publication is done from the clip's page, after approval.

## 8. Frequently asked questions

**I left the setup halfway, what happens?**
The screens already left are saved. The visit comes back at the next sign-in as long as it has not
been finished or skipped. You can also take everything up again in Settings → Live.

**Can I change camera after the first live?**
Yes. Save the new address in Settings → Live; it will be read at the next start of the live. You do
not change input during a live in progress.

**The camera test fails while the picture arrives in OBS.**
The test reads the stream for a few seconds only, from the back office server. Check the address, the
network and the number of connections already open on the camera, then try again. The test changes no
setting.

**Do I have to redo the visit if the mosque name changes?**
No. The name and the time zone are changed in Settings → Mosque; the visit only serves for the input,
the destinations and the bucket.

**Are the dots in a destination address normal?**
Yes. The stream key is a secret: it is never shown again. Send the dots back to keep it, or enter a
complete address to replace it.
