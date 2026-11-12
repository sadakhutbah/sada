# Recordings and S3 buckets

This document is for the mosque administrator. It explains what a clip is, where it is published on
the machine, how to find a clip again in the lists, and how to send the recordings of the live to a
storage bucket — an optional setting.

---

## 1. What a clip is

A clip is a **short excerpt of a khutba**, made after the live from the recording. It is not cut by
hand: the post-live processing spots the passages that stand on their own, cuts them and subtitles
them. The number of clips requested is set in **Settings → Processing** (three by default, from one
to ten); the setting applies at the next pipeline run, not to clips already produced. The
**Produce clips after the live** checkbox, when cleared, stops the cutting: the transcript, notes
and verses are still produced, and the number entered is kept for when it is switched back on.

| Character | What it gives |
| --- | --- |
| Format | vertical, 1080 × 1920, for the phone |
| Length | from 30 to 90 seconds |
| Subtitles | in French only, burned into the picture |
| Music | none |
| Number | three by default, adjustable from 1 to 10 in **Settings → Processing** |
| Types | verse, reminder, quote, passage, conclusion |
| Starting status | **To review** |

Each clip carries a number and a version. Correcting the subtitles and then regenerating produces a
new version, which goes through approval again: a version that nobody has read is never published.

The statuses are **To review**, **Approved**, **Rejected** and **Published**. A rejected clip stays
in the history; it can be put back in the review queue. Publication on the networks is a separate
action, described in the document **Connectors: Facebook, YouTube, Instagram**.

## 2. The published local folder

The **Local folder** connector is active by default. It depends on no network: it copies the approved
clip and its record into the mosque's publishing folder.

| What is deposited | Detail |
| --- | --- |
| The clip | an MP4 file, in a subfolder carrying the date of the khutba |
| The record | a JSON file of the same name, which describes the clip |

The file name is stable: it reuses the mosque name, the live identifier, the session number, the
clip number, its type and its version. It is the same name in the folder and behind the public
address used by Instagram.

The JSON record contains the mosque, the khutba, the date, the source live, the number, the type, the
version, the start and end seconds, the reason for the selection, the name of the source file, the
caption, the person who approved, the one who published, the publication date, the absence of music
and the mention “subtitles in French only”.

The folder is set in **Settings → Folders**, line **Publishing folder**. A folder is proposed by
default; you can change it. By default, the same folder serves the local connector and the Instagram
archive; each then keeps its own field.

The copy does not move the original: the clip in the working folder stays in place.

## 3. Finding a clip again: the pagination of the lists

The **Clips** page shows clips from the most recent to the oldest, in pages of **twenty**. At the
bottom of the list, a line says where you are — for example “20 of 63 clips” — and a button
**Show the next N** adds the next page without erasing the previous one.

The status tabs filter the list and each show their count: **All**, **To review**, **Approved**,
**Published**, **Rejected**. Changing tab starts again from the first page; the counter at the bottom
updates on the chosen filter.

Two other lists have their own limit:

| List | What it shows |
| --- | --- |
| **Publishing → History** | the last hundred publication attempts, without pagination |
| **Log** | the recent actions, in pages of eight, with Previous and Next |

A long list is not a storage problem: the clips stay on the machine even when the page shows only
twenty of them.

## 4. The optional upload to a bucket

After a live stops, the orchestrator can send the **live files** to an S3-compatible bucket. Two
things must be distinguished:

| What goes where | Content |
| --- | --- |
| Storage bucket | the complete recording of the live, and its clean copy when it exists |
| Publishing folder | the approved short clips, with their record |

The bucket therefore does **not** receive the clips. It keeps the raw material of the live, for the
archive or for another use.

The **Send the replays** box is **unticked by default**. As long as it is, no file leaves the
machine. It is the safest setting: a live can represent several gigabytes, and the upload uses the
mosque's connection. You enable it when you know where the files must go and who can read them.

The fields, in **Settings → Replays**:

| Field | What goes in it |
| --- | --- |
| Service | the address of the storage service; empty for AWS |
| Bucket | the name of the bucket, in lower case |
| Region | the region of the service; `auto` suits Cloudflare R2 |
| Access key | the identifier of the key created at the provider |
| Secret key | the secret of that key, hidden after saving |
| Folder in the bucket | the prefix of the files; `rediffusions/` by default |
| Send the replays (optional) | the box that actually enables the upload |

The **Test the write** button deposits a small text file in the chosen folder, then removes it. It is
not content with checking that the bucket exists: it checks that the key can **write**, then
**delete**. The success message names the bucket and the prefix.

The setting is read at the orchestrator's startup. A change made afterwards applies to the next live,
not to the one running.

## 5. Preparing the bucket at the provider

The back office creates neither the bucket nor the key: that is done at the provider, before
entering anything.

| Provider | Service address | Region | To prepare |
| --- | --- | --- | --- |
| Amazon S3 | leave empty | the bucket's region, for example `eu-west-3` | a bucket and an IAM key limited to that bucket |
| Cloudflare R2 | `https://<compte>.r2.cloudflarestorage.com` | `auto` | a bucket and an S3 token with write rights |
| MinIO, Backblaze | the service address, without a path | the service's one | a bucket and an access key |

Three rules avoid most failures:

- **The bucket must already exist.** An invented name gives `NoSuchBucket`.
- **The service address takes no path.** It stops at the host; the bucket name has its own field.
- **The key must be able to write and delete.** The test deposits then removes a file; a read-only key
  fails even though the bucket is good.

The prefix is a folder in the bucket, not a folder on the machine. There is nothing to create: the
prefix appears with the files. `rediffusions/` keeps them apart, without mixing them with other uses
of the same bucket.

The bucket does not need to be public: the back office reads nothing from it in a web page. A private
bucket is even preferable, since it contains the picture and the sound of a khutba.

## 6. What happens on failure

An upload failure never brings the live down: the upload goes in a separate thread, after the stop,
and the engine does not wait for the network.

| Message in the log | Probable cause | What to do |
| --- | --- | --- |
| `Le compartiment n'existe pas (nom ou compte).` | wrongly entered name, or wrong account | check the name and the service address |
| `Clé d'accès inconnue du service.` | wrong or deleted key identifier | create a key again and enter it |
| `Clé secrète refusée (signature invalide).` | wrong secret, or key copied only in part | enter the secret again in full |
| `Le service refuse l'écriture : droits du jeton insuffisants.` | read-only key, or limited rights | give the write and delete rights |
| `Service injoignable : vérifiez l'adresse et le réseau.` | wrong service address, or network down | check the address, without a path, and the connection |
| `Délai dépassé en joignant le service.` | slow network or blocked service | try again at the next live |
| `Nom de compartiment invalide pour ce service.` | capitals, spaces, underscores | rename in lower case, digits, dots and dashes |
| `Certificat refusé : vérifiez l'adresse (https).` | service certificate, or address in `http://` | use the service's `https://` address |

What is not sent is not lost: the files stay on the machine. The local index notes only the
successful uploads, so a failed file is taken again at the next live stop. A file already sent and
unchanged is skipped, which avoids using the connection twice.

If the back office is unreachable at startup, the log says
`réglage du compartiment injoignable … — envoi désactivé` : the live runs normally, without upload.

## 7. What the administrator must check

| Point | What you should see |
| --- | --- |
| The box | **Send the replays** ticked, then settings saved |
| The test | `Écriture acceptée dans « … » (…)` |
| The first upload | in the log, `envoi de … vers compartiment/rediffusions/…`, then `envoyée` |
| The files | in the bucket, under the prefix, with the same name as on the machine |
| The sizes | not zero; a 0-byte file is ignored and reported |
| The originals | still present on the machine |

The log says what has gone. A successful test proves that the key writes and deletes; it does not
prove that the live files will be small enough or that the connection will hold.
The first real check is therefore the one after the first live.

## 8. Frequently asked questions

**Does the bucket receive the clips?**
No. It receives the recording of the live and its clean copy. Approved clips go into the publishing
folder, then, if you decide so, onto the networks.

**Can I empty the bucket?**
Yes. No page of the back office reads its content: the clips and their records stay in the local
folder, and the publication history stays in the database. Emptying the bucket breaks nothing.

**Why two files for one live?**
The orchestrator records the live, and sometimes a clean copy without the screens' dressing. Both go
when they exist, under two different names.

**I changed bucket, the old files do not come back up.**
That is normal. The local index remembers which files have already gone, without distinguishing the
destination: they are not sent again to the new bucket. Only the following lives will be deposited
there.

**Does the upload delete the files from the machine?**
No. It is a copy to the bucket; the recordings and the clips stay in place.
