"""Échantillonneur de consommation : CPU (millicœurs) et mémoire (Mio) de chaque pod, toutes les N secondes.

Lit l'API metrics.k8s.io avec le compte de service du pod ; une ligne CSV par pod et par mesure,
dans <dossier>/AAAA-MM-JJ.csv (un fichier par jour). Sert à dimensionner le direct et les traitements."""

import csv
import datetime as dt
import json
import os
import ssl
import time
import urllib.request

API = "https://kubernetes.default.svc"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
NS = open(f"{SA}/namespace").read().strip()
OUT = os.environ.get("SAMPLER_DIR", "/app/data/metrics")
EVERY = int(os.environ.get("SAMPLER_EVERY", "15"))
CTX = ssl.create_default_context(cafile=f"{SA}/ca.crt")


def cpu_m(v: str) -> float:
    for suf, k in (("n", 1e-6), ("u", 1e-3), ("m", 1.0)):
        if v.endswith(suf):
            return float(v[:-1]) * k
    return float(v) * 1000


def mem_mi(v: str) -> float:
    for suf, k in (("Ki", 1 / 1024), ("Mi", 1.0), ("Gi", 1024.0)):
        if v.endswith(suf):
            return float(v[:-2]) * k
    return float(v) / 1048576


def pods() -> list[dict]:
    req = urllib.request.Request(
        f"{API}/apis/metrics.k8s.io/v1beta1/namespaces/{NS}/pods",
        headers={"Authorization": "Bearer " + open(f"{SA}/token").read().strip()},
    )
    with urllib.request.urlopen(req, context=CTX, timeout=10) as r:
        return json.load(r)["items"]


os.makedirs(OUT, exist_ok=True)
while True:
    now = dt.datetime.now().astimezone()
    try:
        rows = [
            (
                now.isoformat(timespec="seconds"),
                p["metadata"]["name"],
                round(sum(cpu_m(c["usage"]["cpu"]) for c in p["containers"])),
                round(sum(mem_mi(c["usage"]["memory"]) for c in p["containers"])),
            )
            for p in pods()
        ]
        path = os.path.join(OUT, now.strftime("%Y-%m-%d") + ".csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "pod", "cpu_m", "mem_mi"])
            w.writerows(rows)
    except Exception as e:  # noqa: BLE001
        print("sampler:", e, flush=True)
    time.sleep(EVERY)
