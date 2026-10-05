#!/usr/bin/env bash
# Run the MBPP agent on several tasks, validate each one with the moulinette
# and append every run to bench/mbpp/history.jsonl: pass rates, failure
# reasons and metrics are then kept across sessions, and the failing tasks
# can be replayed after a change to the agent.
#
# Usage: ./bench_mbpp.sh [-n N] [-m MODEL] [-l LABEL] [-t TASK_ID]... [--failed] [--stats]
#   -n N         number of random tasks (default 5)
#   -m MODEL     model forwarded to the agent (default: the agent's own)
#   -l LABEL     tag stored with each run, e.g. "prompt-v2" for an ablation
#   -t TASK_ID   run this task instead of a random one (repeatable)
#   --failed     rerun every task whose last run failed (for MODEL if -m)
#   --stats      only print the statistics of the history

set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BENCH="$ROOT/bench/mbpp"
HISTORY="$BENCH/history.jsonl"
TIME_LIMIT=120  # MBPP timeout from the subject (VI.1.1)
PAUSE=5         # seconds between tasks, to spare the free-tier rate limits

RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'

N=5 MODEL="" LABEL="" FAILED=0 STATS_ONLY=0 TASKS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n) N="$2"; shift 2 ;;
        -m) MODEL="$2"; shift 2 ;;
        -l) LABEL="$2"; shift 2 ;;
        -t) TASKS+=("$2"); shift 2 ;;
        --failed) FAILED=1; shift ;;
        --stats) STATS_ONLY=1; shift ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (see --help)" >&2; exit 1 ;;
    esac
done

mkdir -p "$BENCH"
touch "$HISTORY"

# Every read or write of the history goes through this Python helper:
#   record RUN_DIR TASK_ID MODEL LABEL RESULT REASON SESSION -> prints REASON
#   failed MODEL   -> ids of the tasks whose last run failed
#   stats [SESSION]
history() {
    python3 - "$HISTORY" "$@" <<'EOF'
import json
import sys
from collections import Counter, defaultdict

path, cmd, *args = sys.argv[1:]
with open(path) as f:
    runs = [json.loads(line) for line in f if line.strip()]


def summary(rs):
    ok = sum(r["result"] == "PASSED" for r in rs)

    def avg(key):
        values = [r[key] for r in rs if r.get(key) is not None]
        return f"{sum(values) / len(values):.0f}" if values else "-"
    return (f"{ok}/{len(rs)} ({100 * ok / len(rs):.0f}%)  "
            f"iter {avg('iterations')}  in {avg('input_tokens')}  "
            f"out {avg('output_tokens')}  time {avg('time_s')}s")


if cmd == "record":
    run_dir, task_id, model, label, result, reason, session = args
    try:
        with open(f"{run_dir}/solution.json") as f:
            sol = json.load(f)
    except (OSError, ValueError):
        sol = {}
    if reason == "correctness":
        # The agent only submits once the public tests pass: a wrong
        # submission means a hidden test failed
        reason = "hidden tests" if sol.get("success") \
            else f"no answer: {(sol.get('error') or '')[:80]}"
    steps = sol.get("steps") or [{}]
    run = {
        "session": session,
        "task_id": task_id,
        "model": model or steps[-1].get("model_name") or "default",
        "label": label,
        "result": result,
        "reason": reason,
        "iterations": sol.get("iterations"),
        "input_tokens": sol.get("total_input_tokens"),
        "output_tokens": sol.get("total_output_tokens"),
        "time_s": round(sol.get("total_time_seconds") or 0, 1),
        "dir": run_dir,
    }
    with open(path, "a") as f:
        f.write(json.dumps(run) + "\n")
    print(reason)

elif cmd == "failed":
    last = {r["task_id"]: r for r in runs
            if not args[0] or r["model"] == args[0]}
    print(" ".join(t for t, r in last.items() if r["result"] != "PASSED"))

elif cmd == "stats":
    if not runs:
        sys.exit("No run recorded yet.")
    current = [r for r in runs if args and r["session"] == args[0]]
    if current:
        print(f"This session: {summary(current)}")
        for r in current:
            if r["result"] != "PASSED":
                print(f"  task {r['task_id']}: {r['reason']}")

    groups = defaultdict(list)
    for r in runs:
        groups[(r["model"], r["label"])].append(r)
    print("\nHistory by model [label]:")
    for (model, label), rs in sorted(groups.items()):
        print(f"  {model}{f' [{label}]' if label else ''}: {summary(rs)}")

    reasons = Counter(r["reason"].split(":")[0] for r in runs
                      if r["result"] != "PASSED")
    if reasons:
        print("Failure reasons: " + ", ".join(
            f"{k} x{v}" for k, v in reasons.most_common()))

    by_task = defaultdict(list)
    for r in runs:
        by_task[r["task_id"]].append(r)
    failing = {t: rs for t, rs in by_task.items()
               if rs[-1]["result"] != "PASSED"}
    print(f"\nTasks whose last run failed ({len(failing)}), "
          f"rerun them with --failed:")
    for t, rs in failing.items():
        fails = sum(r["result"] != "PASSED" for r in rs)
        print(f"  {t}: failed {fails}/{len(rs)}, last: {rs[-1]['reason']}"
              f"\n      {rs[-1]['dir']}")
EOF
}

if (( STATS_ONLY )); then
    history stats
    exit
fi

if (( FAILED )); then
    read -ra TASKS <<< "$(history failed "$MODEL")"
    if (( ${#TASKS[@]} == 0 )); then
        echo "No failing task to rerun."
        exit
    fi
fi
# No explicit task: an empty id makes the moulinette draw a random one
if (( ${#TASKS[@]} == 0 )); then
    for ((k = 0; k < N; k++)); do TASKS+=(""); done
fi

SESSION="$(date +%Y-%m-%d_%H-%M-%S)"
mkdir -p "$BENCH/$SESSION"
MODEL_ARGS=()
[[ -n "$MODEL" ]] && MODEL_ARGS=(--model-name "$MODEL")

i=0
for task in "${TASKS[@]}"; do
    (( i > 0 )) && sleep "$PAUSE"
    ((i++))
    echo -e "\n${YELLOW}=== Task $i/${#TASKS[@]} ===${NC}"

    TMP="$BENCH/$SESSION/task.json"
    if ! (cd "$ROOT/moulinette" && uv run moulinette_eval dump mbpp \
            ${task:+--task_id "$task"} --output "$TMP") > /dev/null; then
        echo -e "${RED}Could not dump task ${task:-(random)}${NC}"
        continue
    fi
    TASK_ID=$(python3 -c 'import json, sys
print(json.load(open(sys.argv[1]))["task_id"])' "$TMP")
    RUN="$BENCH/$SESSION/$i-$TASK_ID"
    mkdir -p "$RUN" && mv "$TMP" "$RUN/task.json"

    (cd "$ROOT" && timeout "$TIME_LIMIT" uv run -m agent_mbpp \
        --task-file "$RUN/task.json" --output "$RUN/solution.json" \
        "${MODEL_ARGS[@]}") 2>&1 | tee "$RUN/agent.log"
    STATUS=${PIPESTATUS[0]}

    if (( STATUS == 124 )); then
        REASON="timeout"
    elif (( STATUS != 0 )) || [[ ! -f "$RUN/solution.json" ]]; then
        REASON="crash: exit code $STATUS, see agent.log"
    elif (cd "$ROOT/moulinette" && uv run moulinette_eval validate mbpp \
            "$RUN/task.json" "$RUN/solution.json") > "$RUN/validate.log" 2>&1; then
        REASON=""
    elif grep -q "Correctness:.*FAILED" "$RUN/validate.log"; then
        REASON="correctness"
    elif grep -q "Metrics:.*INVALID" "$RUN/validate.log"; then
        # First limit the moulinette reports as exceeded, colours stripped
        REASON="metrics: $(sed 's/\x1b\[[0-9;]*m//g' "$RUN/validate.log" \
            | grep -m1 '^  - ' | sed 's/^  - //')"
    else
        # The moulinette itself crashed (Docker down...): not the agent's fault
        REASON="validation crash: $(tail -n1 "$RUN/validate.log")"
    fi

    RESULT=PASSED
    [[ -n "$REASON" ]] && RESULT=FAILED
    REASON=$(history record "$RUN" "$TASK_ID" "$MODEL" "$LABEL" \
        "$RESULT" "$REASON" "$SESSION")
    if [[ $RESULT == PASSED ]]; then
        echo -e "${GREEN}Task $TASK_ID: PASSED${NC}"
    else
        echo -e "${RED}Task $TASK_ID: FAILED ($REASON)${NC}"
    fi
done

echo -e "\n${YELLOW}==============================================${NC}"
history stats "$SESSION"
