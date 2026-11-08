.PHONY: setup test demo demo-short pipeline clean backoffice
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

# Back-office web (http://<machine>:8090) ; créer d'abord un admin : $(PY) -m minbar.backoffice create-admin --username admin
backoffice:
	$(PY) -m minbar.backoffice serve

pipeline:
	$(PY) -m minbar.pipeline.run
