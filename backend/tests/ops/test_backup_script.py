"""OR-6: the daily backup's retention and its "already taken today" rule, run from the script itself
(scripts/backup.sh, loaded as a library) on a directory of dated files. Seven days are kept, by
the date in the file name, never by mtime; anything else in the directory is left alone, and a
half-written file never counts as a backup."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"


def run(directory: Path, call: str) -> str:
    done = subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"; {call}'],
        env={"BACKUP_LIB": "1", "BACKUP_DIR": str(directory), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def dated(directory: Path, *days: str) -> None:
    for day in days:
        (directory / f"auction-{day}.dump").write_bytes(b"dump")
        (directory / f"media-{day}.tgz").write_bytes(b"tgz")


def test_seven_days_are_kept_by_the_date_in_the_name(tmp_path: Path) -> None:
    days = [f"2026-10-{d:02d}" for d in range(10, 21)]  # the 10th to the 20th
    dated(tmp_path, *days)
    (tmp_path / "notes.txt").write_text("not a backup")
    (tmp_path / "auction-latest.dump").write_bytes(b"no date: not ours to delete")
    run(tmp_path, "prune 2026-10-20")
    kept = sorted(p.name for p in tmp_path.iterdir())
    assert kept == sorted(
        [f"auction-2026-10-{d}.dump" for d in range(14, 21)]
        + [f"media-2026-10-{d}.tgz" for d in range(14, 21)]
        + ["notes.txt", "auction-latest.dump"]
    )


def test_retention_counts_days_across_a_month_end(tmp_path: Path) -> None:
    dated(tmp_path, "2026-10-26", "2026-10-27", "2026-11-01", "2026-11-02")
    run(tmp_path, "prune 2026-11-02")
    assert sorted(p.name.split("-", 1)[1] for p in tmp_path.glob("auction-*")) == [
        "2026-10-27.dump",
        "2026-11-01.dump",
        "2026-11-02.dump",
    ]


def test_a_day_counts_as_backed_up_only_with_a_whole_dump(tmp_path: Path) -> None:
    assert run(tmp_path, "taken 2026-10-20 && echo yes || echo no") == "no"
    (tmp_path / ".auction-2026-10-20.dump.partial").write_bytes(b"half")  # interrupted
    assert run(tmp_path, "taken 2026-10-20 && echo yes || echo no") == "no"
    (tmp_path / "auction-2026-10-20.dump").write_bytes(b"whole")
    assert run(tmp_path, "taken 2026-10-20 && echo yes || echo no") == "yes"


def test_a_partial_file_left_by_an_interrupted_run_is_cleared(tmp_path: Path) -> None:
    (tmp_path / ".auction-2026-10-19.dump.partial").write_bytes(b"half")
    (tmp_path / ".media-2026-10-19.tgz.partial").write_bytes(b"half")
    run(tmp_path, "prune 2026-10-20")
    assert list(tmp_path.iterdir()) == []


def test_backups_are_readable_by_their_owner_only(tmp_path: Path) -> None:
    """A dump holds every account and its password hash: no other user on the host reads it."""
    assert run(tmp_path, "umask") == "0077"
