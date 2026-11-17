"""Mawaqit cache: freshness follows the calendar day, not only the age."""


def test_yesterdays_cache_is_never_fresh():
    """Prayer times move every day (an hour at the clock change): a young cache from yesterday is refetched."""
    import time as t_

    from sada import mawaqit

    old = {"jumua": "12:30", "times": ["06:30"]}
    new = {"jumua": "12:30", "times": ["06:32"]}
    now = t_.mktime((2026, 11, 17, 0, 5, 0, 0, 0, -1))  # 00:05: a cache 15 min old was fetched yesterday
    data, stored = mawaqit.resolve("https://x", dict(old), 900, fetch=lambda u, t: dict(new), now=now)
    assert data["origin"] == "live" and stored["times"] == ["06:32"]
    data, stored = mawaqit.resolve("https://x", dict(new), 240, fetch=lambda u, t: 1 / 0, now=now)
    assert stored is None and data["times"] == ["06:32"]  # same day: the cache is used
