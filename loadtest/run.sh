#!/usr/bin/env bash
# The load test (Phase 11, loadtest/README.md), end to end, on the running dev stack at its
# production limits. From the repository root:
#   docker compose up -d --build
#   bash loadtest/run.sh              # about 10 minutes; loadtest-setup's options pass through
# 1. loadtest-setup: the lot and its bidders, signed in (tokens in loadtest/.run/plan.json only)
# 2. loadtest-watch, beside k6: the slow log (NFR-1), the ledger's first sight of each bid
#    (NFR-7), the room's presence (NFR-5)
# 3. k6 runs room.js through the web service (NFR-2 to NFR-6)
# 4. both api instances' logs; the tests the report cites (NFR-1, NFR-6, NFR-7, NFR-28, OR-15)
# 5. loadtest-report: the ledger checks (NFR-28), then results/<date>.json and RESULTS.md
# Everything the run leaves besides those two files stays in loadtest/.run (gitignored).
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: leave the container paths alone
cd "$(dirname "$0")/.."

LT=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.loadtest.yml --profile loadtest)
RUN=loadtest/.run
mkdir -p "$RUN"
rm -f "$RUN"/{events.log,summary.json,watch.json,api.log,api-2.log,pytest.txt}

# the machine: the host's CPU and RAM, the resources of Docker's VM, the k6 version
if command -v powershell.exe >/dev/null; then
  powershell.exe -NoProfile -Command '$c = Get-CimInstance Win32_Processor | Select-Object -First 1; [ordered]@{cpu = $c.Name.Trim(); cores = $c.NumberOfCores; threads = $c.NumberOfLogicalProcessors; ram_bytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory; os = (Get-CimInstance Win32_OperatingSystem).Caption} | ConvertTo-Json -Compress' >"$RUN/host.json"
else
  printf '{"cpu": "%s", "threads": %s, "ram_bytes": %s, "os": "%s"}\n' \
    "$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)" "$(nproc)" \
    "$(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)" "$(uname -sr)" >"$RUN/host.json"
fi
docker info --format '{{json .}}' >"$RUN/docker-info.json"
"${LT[@]}" run --rm -T k6 version >"$RUN/k6-version.txt"

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"${LT[@]}" run --rm -T loadtest-tools python -m app.cli loadtest-setup "$@"
"${LT[@]}" run --rm -T loadtest-tools python -m app.cli loadtest-watch </dev/null &
watcher=$!
k6_status=0
"${LT[@]}" run --rm -T k6 run --quiet --log-format=json --log-output=file=/loadtest/.run/events.log /loadtest/room.js || k6_status=$?
watch_status=0
wait "$watcher" || watch_status=$? # 1: the lot was not settled in time; the report shows it

for api in api api-2; do
  docker compose logs --no-color --no-log-prefix --since "$since" "$api" >"$RUN/$api.log"
done
if [ "$k6_status" -ne 0 ]; then
  echo "k6 failed (exit $k6_status), so there is nothing to report: see $RUN" >&2
  exit "$k6_status"
fi
cited=$("${LT[@]}" run --rm -T loadtest-tools python -m app.cli loadtest-report --cited)
# shellcheck disable=SC2086 # one argument per test id
docker compose exec -T api pytest -s -rA -p no:cacheprovider $cited >"$RUN/pytest.txt" 2>&1 || true
"${LT[@]}" run --rm -T loadtest-tools python -m app.cli loadtest-report
exit "$watch_status"
