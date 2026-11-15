"""The post-live runner records the pipeline's current stage while it runs."""

import sys

from sada.backoffice import traitement


def test_lancer_records_steps_and_keeps_the_output_tail(tmp_path):
    script = (
        "import os,sys\n"
        "assert os.environ.get('SADA_REPORT_STEPS')\n"
        "print('@@step audio'); print('hello'); print('@@step verses'); print('boom', file=sys.stderr); sys.exit(3)\n"
    )
    code, tail = traitement._lancer([sys.executable, "-c", script], {}, tmp_path, 0)
    state = traitement.lire(tmp_path)["0"]
    assert code == 3
    assert state["step"] == "verses" and state["step_at"]
    assert "hello" in tail and "boom" in tail and "@@step" not in tail
