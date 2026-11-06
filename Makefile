.PHONY: setup test demo demo-short pipeline clean
PY=.venv/bin/python

setup:
	uv venv -q .venv && uv pip install -q -p $(PY) -e . pyzmq
	@test -x tools/bin/mediamtx || (echo "mediamtx manquant : voir README (tools/bin/)" && exit 1)

test:
	$(PY) -m pytest -q

# Démo complète : caméra simulée + « Facebook » local + orchestrateur à horloge accélérée (~45 min, 2 khutbas)
demo:
	./demo.sh

# Démo courte : extrait de 5 min de khutba (~15 min)
demo-short:
	CUT=300 ./demo.sh

pipeline:
	$(PY) -m minbar.pipeline.run $$(ls -td data/state/*/ | head -1)sessions.json
