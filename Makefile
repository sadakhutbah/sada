.PHONY: setup test pipeline backoffice front
PY=.venv/bin/python

setup:
	uv venv -q .venv && uv pip install -q -p $(PY) -e .
	@test -x tools/bin/mediamtx || echo "note: tools/bin/mediamtx missing (needed for live streaming, see README)"

test:
	$(PY) -m pytest -q

# Back office: http://<host>:8090 (create an admin first, see README)
backoffice:
	$(PY) -m sada.backoffice serve

pipeline:
	$(PY) -m sada.pipeline.run

# Rebuild the React front end into web/dist
front:
	npm --prefix web run build
