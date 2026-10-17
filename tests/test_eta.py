import pytest

from sk_public_money import eta


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_expected_prefers_this_machines_history(cache):
    assert eta.kind("fs") == "first"
    assert eta.expected("fs", "first", True, {}) == 15 * 60  # built-in first build
    assert eta.expected("uvo", "first", True, {}) == 1.5 * 60  # from the snapshot
    assert eta.expected("uvo", "first", False, {}) == 45 * 60  # --no-bootstrap
    assert eta.expected("fs", "update", True, {}) == 60
    eta.remember("fs", "first", 600)
    eta.remember("fs", "update", 12)
    history = eta.load_history()
    assert eta.expected("fs", "first", True, history) == 600
    assert eta.expected("fs", "update", True, history) == 12
    (cache / "fs").mkdir()
    (cache / "fs" / "fs.sqlite").write_bytes(b"x")
    assert eta.kind("fs") == "update"


def test_progress_from_log_lines():
    assert eta.progress("2026-10-17 15:02:09 [13250/13407] procurement details") == pytest.approx(13250 / 13407)
    assert eta.progress("dump 45 %, 1246825 raw notices read") == 0.45
    assert eta.progress("procurement_details: 131 of 13407 procurements") == pytest.approx(131 / 13407)
    assert eta.progress("ds_dphv: unchanged") is None


def test_remaining_blends_time_and_progress():
    assert eta.remaining(60, 600) == 540  # no progress logged: expected time minus elapsed
    assert eta.remaining(900, 600) == 60  # overdue: still a little left, never zero
    # Half done after 10 min: progress says 10 more, time says 0 more (floored); half-and-half.
    assert eta.remaining(600, 600, "dump 50 %") == pytest.approx(0.5 * 600 + 0.5 * 60)


def test_total_simulates_the_job_slots():
    assert eta.total([600, 60], [300, 300], jobs=2) == 660  # both waiting ones queue behind the short one
    assert eta.total([], [100, 100, 100], jobs=4) == 100
    assert eta.total([], [], jobs=4) == 0


def test_human():
    assert eta.human(20) == "<1 min"
    assert eta.human(300) == "~5 min"
    assert eta.human(4500) == "~1 h 15 min"


def test_eu_updates_reload_their_lists_and_overdue_runs_say_so(cache):
    assert eta.expected("eu", "update", True, {}) == 5.5 * 60
    assert eta.overdue(400, 60) and not eta.overdue(60, 60)
    assert not eta.overdue(400, 60, "[3/34] files")  # progress logged: the estimate follows it instead
