.PHONY: setup test demo demo-short pipeline clean backoffice bac front
PY=.venv/bin/python

setup:
	uv venv -q .venv && uv pip install -q -p $(PY) -e .
	@test -x tools/bin/mediamtx || (echo "mediamtx manquant : voir README (tools/bin/)" && exit 1)

test:
	$(PY) -m pytest -q

# Démo complète : caméra simulée + « Facebook » local + orchestrateur à horloge accélérée (~45 min, 2 khutbas)
demo:
	./demo.sh

# Démo courte : extrait de 5 min de khutba (~15 min)
demo-short:
	CUT=300 ./demo.sh

# Bac à sable : écrans rendus en direct (http://127.0.0.1:8088) + tableau de bord (8090, base data/sandbox/bo.db)
bac:
	$(PY) tools/bac_a_sable.py

# Back-office web (http://<machine>:8090) ; créer d'abord un admin : $(PY) -m sada.backoffice create-admin --username admin
backoffice:
	$(PY) -m sada.backoffice serve

pipeline:
	$(PY) -m sada.pipeline.run

# Front React du back-office : reconstruit web/dist, servi par le serveur à la racine
front:
	npm --prefix web run build
