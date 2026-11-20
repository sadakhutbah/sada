# Migrating the persistent data to the Hetzner volume `sada-data`

Status: **prepared, not applied**. To be run off-air, with the owner's go-ahead.

## Why

The 50 GB volume is attached and mounted on `/mnt/sada-data`, but empty. Everything persistent is
on the 150 GB root disk, because the local-path provisioner of k3s writes under
`/var/lib/rancher/k3s/storage`. The `HelmChartConfig` that the playbook used to drop was
ineffective (k3s ships local-path as a plain manifest, not as a Helm chart), so the volume never
received any data. Losing the server would lose the data.

## Inventory (read-only, taken on the live server)

| What | Where | Size |
| --- | --- | --- |
| App data PVC `sada-data` (recordings, state, output, models, metrics, back office) | `/var/lib/rancher/k3s/storage/pvc-<id>_sada_sada-data` | ~7.5 GB |
| PostgreSQL PVC `sada-postgres-1` (CloudNativePG) | `/var/lib/rancher/k3s/storage/pvc-<id>_sada_sada-postgres-1` | ~0.6 GB |
| Backups | `/root/backups` | ~6.9 GB |
| Container images (containerd) | `/var/lib/rancher/k3s/agent/containerd` | ~38 GB |
| MediaMTX `receiver` | ConfigMap only, no persistent data | - |

StorageClass `local-path` (default, `Delete` reclaim policy), 2 PVs, both `Bound`. The volume is
`/dev/sdb` (ext4, 49 GB), mounted from `/etc/fstab` by UUID with `nofail`; it only holds an empty
`k3s/` directory.

Out of scope on purpose: the **image cache stays on the root disk** (38 GB would not fit with the
data on a 50 GB volume, and layer reads are faster on the local disk). It can be pruned
separately (`k3s crictl rmi --prune`).

## Method

A **bind mount** of `/mnt/sada-data/k3s` onto `/var/lib/rancher/k3s/storage`.

- The PV objects hold that absolute path and it is immutable: moving it through Kubernetes would
  mean recreating PVs and PVCs (with a `Delete` reclaim policy, a risky road).
- With the bind mount nothing changes in Kubernetes, the chart or `sada-gitops`: no release, no
  Flux action, no PV edit. The whole change is on the host (fstab, a systemd drop-in), and is
  already described by `deploy/ansible/playbook.yml` for fresh clusters.
- Rollback is trivial: the old data stays untouched on the root disk, hidden under the mount.
  `umount` shows it again.
- A drop-in makes `k3s.service` require both mounts: without the volume, k3s does not start
  (rather than writing into an empty directory on the root disk).

## Downtime

Back office, orchestrators, receiver (RTMP/SRT ingest) and PostgreSQL are stopped between steps 3
and 6. Estimate: **10 to 15 minutes** (final rsync of the delta ~1 min, checksum verification
~3 to 5 min, mount and restart ~3 min, checks). The long copy (step 2) is done live beforehand and
causes no interruption. Never run it during a live broadcast.

## Procedure (as root on `sada-app`; kubectl with the repository kubeconfig)

Variables used below:

```
SRC=/var/lib/rancher/k3s/storage
DST=/mnt/sada-data/k3s
```

### 0. Preconditions

1. Owner's approval, no broadcast planned. For each mosque `<slug>`:
   `kubectl -n sada logs deploy/orchestrator-<slug> --tail=20` shows `STOPPED`, or query
   `http://orchestrator-<slug>:8088/status` (state, stream_on) through
   `kubectl -n sada exec deploy/backoffice -c backoffice -- python -c ...`.
   Live, INTERMISSION or looping encoder: stop here.
2. `df -h /mnt/sada-data` shows at least 20 GB free; `findmnt /mnt/sada-data` shows `/dev/sdb`.
3. Record the state to compare afterwards:
   `kubectl -n sada get deploy -o custom-columns=N:.metadata.name,R:.spec.replicas > /root/replicas-before.txt`
   and `kubectl -n sada get pods,pvc,pv -o wide > /root/state-before.txt`.

### 1. Verified backup

```
D=$(date +%Y%m%d)
kubectl -n sada exec sada-postgres-1 -c postgres -- pg_dumpall -U postgres > /root/backups/sada-pg-$D-pre-migration.sql
test -s /root/backups/sada-pg-$D-pre-migration.sql && tail -n 3 /root/backups/sada-pg-$D-pre-migration.sql   # ends with "PostgreSQL database cluster dump complete"
gzip -t /root/backups/sada-data-20261119.tgz && echo archive-ok      # or the latest archive
tar -C $SRC -czf /root/backups/sada-data-$D-pre-migration.tgz . && gzip -t /root/backups/sada-data-$D-pre-migration.tgz && sha256sum /root/backups/*pre-migration* > /root/backups/pre-migration.sha256
```

Check also Hetzner side: the volume itself can be snapshotted from the console (optional, billable
per GB).

### 2. Pre-copy, application still running

```
rsync -aHAX --numeric-ids --info=progress2 $SRC/ $DST/
```

PostgreSQL files copied hot are not consistent: this pass only moves the bulk. The final pass
(step 4) is the one that counts.

### 3. Stop (Flux suspended first, so nothing is re-created)

```
flux suspend helmrelease sada -n sada
kubectl -n sada annotate cluster.postgresql.cnpg.io sada-postgres cnpg.io/hibernation=on --overwrite
for d in $(kubectl -n sada get deploy -o name); do kubectl -n sada scale $d --replicas=0; done
kubectl -n sada get pods                       # wait until none is left (the PostgreSQL pod stops cleanly)
findmnt | grep "$SRC" || echo "no more pod mounts"
```

### 4. Final copy and verification

```
rsync -aHAX --numeric-ids --delete $SRC/ $DST/
rsync -aHAXn --checksum --delete --itemize-changes $SRC/ $DST/    # must print nothing
diff <(cd $SRC && find . | sort) <(cd $DST && find . | sort) && echo same-tree
du -s --apparent-size $SRC $DST
```

Any difference: stop, do not switch, go to *Rollback* (nothing was changed yet).

### 5. Switch

```
echo "$DST $SRC none bind,x-systemd.requires-mounts-for=/mnt/sada-data 0 0" >> /etc/fstab
mkdir -p /etc/systemd/system/k3s.service.d
printf '[Unit]\nRequiresMountsFor=/mnt/sada-data %s\n' "$SRC" > /etc/systemd/system/k3s.service.d/10-sada-storage.conf
systemctl daemon-reload
mount $SRC
findmnt $SRC          # source must show /mnt/sada-data/k3s on /dev/sdb
ls $SRC               # both pvc-* directories
```

The line is identical to the one the playbook writes, so a later `ansible-playbook` run finds the
bind already mounted and changes nothing.

### 6. Restart

```
kubectl -n sada annotate cluster.postgresql.cnpg.io sada-postgres cnpg.io/hibernation=off --overwrite
kubectl -n sada wait --for=condition=Ready pod/sada-postgres-1 --timeout=300s
# replicas as in /root/replicas-before.txt (all 1 at the time of writing)
for d in $(kubectl -n sada get deploy -o name); do kubectl -n sada scale $d --replicas=1; done
flux resume helmrelease sada -n sada
```

### 7. Verification

- `kubectl -n sada get pods,ingress,certificate`: everything Running, nothing in error.
- `findmnt | grep pvc-` : the kubelet pod mounts have `/dev/sdb[/k3s/...]` as source.
- `df -h / /mnt/sada-data`: the volume now holds ~8 GB.
- `kubectl -n sada exec sada-postgres-1 -c postgres -- psql -U postgres -c '\l'`, and a row
  count on a main table compared with before.
- `https://console.sadakhutbah.org` answers and login works; each orchestrator logs `STOPPED`
  without error; a recording of the previous days is listed.
- `flux get helmreleases -n sada`: `Ready` and no pending upgrade.
- Optional, in the same window and with the owner's go-ahead: `reboot`, then check that the volume,
  the bind and k3s come back in that order (about 2 more minutes of downtime).

### 8. Backups on the volume

```
mkdir -p /mnt/sada-data/backups && chmod 700 /mnt/sada-data/backups
rsync -aH --info=progress2 /root/backups/ /mnt/sada-data/backups/
(cd /root/backups && sha256sum *) | (cd /mnt/sada-data/backups && sha256sum -c -)
```

From now on, new backups go to `/mnt/sada-data/backups`. The originals in `/root/backups` are
**kept** until the owner agrees to their removal. Note: the volume is protected against loss of the
server (`delete_protection`), not against its own failure or a mistake; an off-site copy remains
desirable and is not part of this change.

## Rollback

- **Before step 5** (failure while copying or verifying): `flux resume`, hibernation `off`, scale
  back up. Nothing was switched; the root disk is intact. The copy in `$DST` can be emptied later.
- **After step 5, before new data matters**: stop pods as in step 3, then
  `umount $SRC`, delete the fstab line and the drop-in, `systemctl daemon-reload`, restart as in
  step 6. The pods then use the original data on the root disk, as it was at step 3.
- **After the apps have written on the volume**: same, after copying back what changed:
  `rsync -aHAX --numeric-ids --delete $DST/ $SRC/` (with `$SRC` unmounted), then restart.
- **Disaster**: restore from `/root/backups/*pre-migration*` (`pg_dumpall` dump and data archive).

## Cleanup (separate decision, after a quiet week and the owner's approval)

The old copy on the root disk still sits under the bind mount (~8 GB), as do `/root/backups`.
To remove it: `mkdir /mnt/rootview && mount --bind / /mnt/rootview` shows the real
`/var/lib/rancher/k3s/storage` of the root disk without the bind on top; check it, then delete it
there only. Nothing is deleted by this procedure.
