"""OR-6's restore drill, the check (backend/scripts/restore-drill.sh runs it after restoring the
drill's backup into a scratch database and its images into a scratch folder):

- every table's rows in the restored database equal the counts taken around the dump, which the
  drill took twice and required equal, so the dump is of one known state;
- the six ledger checks of "Try to break it" (services/demo_checks.py) on the latest verified
  stress run in the restored database, with that run's own acknowledgements, all pass and give
  the same values as on the live database and as recorded at the run;
- every image key in the restored database is in the restored media with the live file's bytes.

The evidence page it writes is published (ops/evidence/): counts, check values and digests only,
no address, token or private path. Its first comment line is the summary the load-test report
reads for OR-6.
"""

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.ops import OpsRepository
from app.services.demo_checks import verify

Sessions = async_sessionmaker[AsyncSession]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def media_check(keys: list[str], restored: Path, live: Path) -> dict[str, Any]:
    """Each key's file in the restored media, byte for byte the live one."""
    problems, digest, matched = [], hashlib.sha256(), 0
    for key in sorted(keys):
        restored_file, live_file = restored / key, live / key
        if not restored_file.is_file():
            problems.append(f"{key}: missing from the restored media")
            continue
        sha = _sha(restored_file)
        digest.update(f"{key} {sha}\n".encode())
        if not live_file.is_file() or _sha(live_file) != sha:
            problems.append(f"{key}: not the live file's bytes")
            continue
        matched += 1
    return {
        "files": len(keys),
        "matched": matched,
        "problems": problems,
        "digest": digest.hexdigest(),
    }


def _values(checks: list[dict[str, Any]]) -> list[tuple[str, bool, Any]]:
    return [(c["id"], c["passed"], c["value"]) for c in checks]


async def check(
    restored_owner: Sessions,
    restored_app: Sessions,
    live_app: Sessions,
    expected: dict[str, int],
    restored_media: Path,
    live_media: Path,
) -> dict[str, Any]:
    async with restored_owner() as session:
        repo = OpsRepository(session)
        counts = await repo.table_counts()
        run = await repo.latest_verified_run()
        keys = await repo.image_keys()
    problems = [
        f"{name}: {expected.get(name, 'no')} before the dump, {counts.get(name, 'no')} restored"
        for name in sorted(set(expected) | set(counts))
        if expected.get(name) != counts.get(name)
    ]
    ledger: dict[str, Any] | None = None
    if run is not None:
        lot, acks, recorded = run
        ack_tuples = [(int(s), str(b), int(a)) for s, b, a in acks]
        restored_checks = await verify(restored_app, lot, ack_tuples)  # as the app role
        live_checks = await verify(live_app, lot, ack_tuples)
        ledger = {
            "auction_id": str(lot),
            "restored": [
                {"id": c["id"], "passed": c["passed"], "value": c["value"]} for c in restored_checks
            ],
            "same_as_live": sorted(_values(restored_checks)) == sorted(_values(live_checks)),
            "same_as_recorded": sorted(_values(restored_checks)) == sorted(_values(recorded)),
        }
    media = media_check(keys, restored_media, live_media)
    passed = (
        not problems
        and ledger is not None
        and all(c["passed"] for c in ledger["restored"])
        and ledger["same_as_live"]
        and ledger["same_as_recorded"]
        and media["files"] > 0
        and not media["problems"]
    )
    return {
        "result": "pass" if passed else "fail",
        "tables": {"expected": expected, "restored": counts, "problems": problems},
        "run": ledger,
        "media": media,
    }


def evidence_markdown(result: dict[str, Any], date: str, timings: dict[str, float]) -> str:
    """ops/evidence/restore-drill-<date>.md."""
    tables, run, media = result["tables"], result["run"], result["media"]
    passed_checks = sum(c["passed"] for c in run["restored"]) if run else 0
    total_checks = len(run["restored"]) if run else 0
    rows = sum(tables["restored"].values())
    n_tables = len(tables["restored"])
    expected, restored = tables["expected"], tables["restored"]
    lines = [
        f"<!-- restore-drill result={result['result']} date={date} tables={n_tables} "
        f"rows={rows} checks={passed_checks}/{total_checks} media={media['files']} -->",
        f"# Restore drill, {date}",
        "",
        f"Result: **{result['result']}**. OR-6: a backup taken by the daily backup's own code "
        "(`backup` in backend/scripts/backup.sh) was restored into a scratch database with "
        "`pg_restore`, its images into a scratch folder, and checked by `python -m app.cli "
        "restore-check`; both were deleted afterwards. Made by backend/scripts/restore-drill.sh.",
        "",
        "Durations: " + ", ".join(f"{k} {v:.1f} s" for k, v in timings.items()) + ".",
        "",
        "## Rows per table",
        "",
        f"{len(tables['restored'])} tables, {rows:,} rows. The counts were taken before and after "
        "the dump and were equal, so the dump is of one state.",
        "",
        "| Table | Before the dump | Restored |",
        "|---|---|---|",
        *[
            f"| {name} | {expected.get(name, 'missing')} | {restored.get(name, 'missing')} |"
            for name in sorted(set(expected) | set(restored))
        ],
        "",
        "## The six ledger checks",
        "",
    ]
    if run is None:
        lines += ["No verified stress run in the restored database: the checks could not run.", ""]
    else:
        lines += [
            f"On the latest verified stress run's lot, `{run['auction_id']}`, with that run's own "
            "acknowledgements, as the application role. Same values as on the live database: "
            f"{'yes' if run['same_as_live'] else 'NO'}; as recorded at the run: "
            f"{'yes' if run['same_as_recorded'] else 'NO'}.",
            "",
            "| Check | Result | Value |",
            "|---|---|---|",
            *[
                f"| {c['id']} | {'pass' if c['passed'] else 'FAIL'} | `{c['value']}` |"
                for c in run["restored"]
            ],
            "",
        ]
    lines += [
        "## Images",
        "",
        f"{media['files']} image files named in the restored database; {media['matched']} present "
        "in the restored media with the live file's exact bytes (SHA-256). Digest of every "
        f"key and file hash: `{media['digest']}`.",
        "",
    ]
    if tables["problems"] or media["problems"]:
        lines += [
            "## Problems",
            "",
            *[f"- {p}" for p in tables["problems"] + media["problems"]],
            "",
        ]
    return "\n".join(lines)
