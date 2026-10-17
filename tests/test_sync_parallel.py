import os
import sys

import pytest

from sk_public_money import localdb, sync

# A fake source: logs a progress line, records when it ran, sleeps, and exits with the code its name ends in.
CHILD = """
import sys, time, pathlib
name, record = sys.argv[1], pathlib.Path(sys.argv[2])
print(f"{name}: 50 % done", flush=True)
with record.open("a") as f:
    f.write(f"start {name} {time.monotonic()}\\n")
time.sleep(0.4)
with record.open("a") as f:
    f.write(f"end {name} {time.monotonic()}\\n")
sys.exit(int(name[-1]))
"""


@pytest.fixture
def fake_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path / "cache"))
    record = tmp_path / "record.txt"
    # Quickest first, like the real table; each name ends in its exit code.
    monkeypatch.setattr(sync, "SOURCES", {"quick0": "", "mid0": "", "broken3": "", "slow0": ""})
    monkeypatch.setattr(sync, "child_command",
                        lambda name, bootstrap: [sys.executable, "-c", CHILD, name, str(record)] + ([] if bootstrap else ["--no-bootstrap"]))
    return record


def events(record):
    rows = [line.split() for line in record.read_text().splitlines()]
    return [(kind, name, float(t)) for kind, name, t in rows]


def test_parallel_runs_slowest_first_and_at_most_jobs_at_once(fake_sources, capsys):
    with pytest.raises(SystemExit) as exc:
        sync.main(["all", "--jobs", "2"])
    assert exc.value.code == 1  # broken3 failed
    rows = events(fake_sources)
    starts = [name for kind, name, _ in sorted(rows, key=lambda r: r[2]) if kind == "start"]
    assert starts[:2] == ["slow0", "broken3"] or set(starts[:2]) == {"slow0", "broken3"}
    assert set(starts) == {"quick0", "mid0", "broken3", "slow0"}
    # Never more than two running at once.
    timeline = sorted(rows, key=lambda r: r[2])
    running = peak = 0
    for kind, _, _ in timeline:
        running += 1 if kind == "start" else -1
        peak = max(peak, running)
    assert peak <= 2
    err = capsys.readouterr().err  # not a terminal: one line per start and finish, then the summary
    assert "slow0: started" in err and "quick0: ok" in err and "broken3: failed (exit 3" in err
    assert err.index("=== summary ===") < err.index("  quick0: ok")
    assert (sync.log_path("slow0")).read_text().strip() == "slow0: 50 % done"


def test_parallel_all_ok_exits_cleanly_and_passes_no_bootstrap(fake_sources, monkeypatch):
    seen = []
    command = sync.child_command
    monkeypatch.setattr(sync, "child_command", lambda name, bootstrap: seen.append(bootstrap) or command(name, bootstrap))
    sync.main(["all", "--skip", "broken3", "--no-bootstrap"])  # default --jobs 4, no failure: no exit
    assert seen == [False, False, False]


def test_jobs_must_be_positive(fake_sources):
    with pytest.raises(SystemExit):
        sync.main(["all", "--jobs", "0"])


def test_last_line_for_the_status(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("first\nfile 3/34\rfile 4/34\n\n")
    assert sync.last_line(log, 80) == "file 4/34"
    assert sync.last_line(log, 5) == "file…"
    assert sync.last_line(tmp_path / "missing.log", 80) == ""


def test_free_space_counts_what_other_running_syncs_claimed(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_PUBLIC_MONEY_CACHE_DIR", str(tmp_path))
    claims = tmp_path / ".claims"
    claims.mkdir()
    free = localdb.shutil.disk_usage(tmp_path).free
    # Another live process (the test runner's parent) claimed nearly all of it.
    (claims / str(os.getppid())).write_text(str(free - (1 << 20)))
    with pytest.raises(OSError, match="claimed by other running syncs"):
        localdb.ensure_free_space(tmp_path, 2 << 20, reserve=0)
    # A claim of a process that has ended is dropped.
    (claims / str(os.getppid())).unlink()
    dead = 2**22 + 12345
    while True:
        try:
            os.kill(dead, 0)
            dead += 1
        except ProcessLookupError:
            break
        except PermissionError:
            dead += 1
    (claims / str(dead)).write_text(str(free))
    localdb.ensure_free_space(tmp_path, 1 << 20, reserve=0)
    assert not (claims / str(dead)).exists()
    assert (claims / str(os.getpid())).read_text() == str(1 << 20)  # this process's own claim


def test_parallel_reports_the_time_left_and_remembers_durations(fake_sources, capsys):
    from sk_public_money import eta

    sync.main(["all", "--skip", "broken3"])
    err = capsys.readouterr().err
    assert "slow0: started" in err and "all done in" in err and "around" in err
    history = eta.load_history()
    assert set(history) == {"quick0:first", "mid0:first", "slow0:first"}
