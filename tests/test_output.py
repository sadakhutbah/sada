"""Clip manifest: runs written before the current format can still be imported."""

import json

from sada.pipeline.output import SessionOutput, clip_entry


def test_old_manifest_reads_as_current(tmp_path):
    o = SessionOutput(tmp_path)
    o.clips.mkdir()
    o.manifest.write_text(
        json.dumps(
            [
                {
                    "kind": "verset",
                    "start": 30.0,
                    "end": 60.0,
                    "file": "clip2_verset_A-VALIDER.mp4",
                    "source_offset_s": 130.0,
                }
            ]
        )
    )
    [c] = o.manifest_entries()
    assert c["idx"] == 2 and c["source_offset"] == 100.0 and c["video_source"] is None


def test_written_manifest_round_trips(tmp_path):
    o = SessionOutput(tmp_path)
    o.clips.mkdir()
    w = {"kind": "conclusion", "why": "fin", "start": 10.0, "end": 50.0}
    o.write_manifest([clip_entry(w, 3, o.clips / "clip3_conclusion_A-VALIDER.mp4", "live.mkv", 12.345, None)])
    [c] = o.manifest_entries()
    assert (c["idx"], c["source_offset"], c["file"], c["video_source"]) == (
        3,
        12.35,
        "clip3_conclusion_A-VALIDER.mp4",
        "live.mkv",
    )
