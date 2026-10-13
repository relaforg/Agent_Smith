#!/usr/bin/env bash
# Run from the directory where `uv run python -m moulinette` works.
#
# usage: ./validate_ablations.sh <ablation_dir> [<ablation_dir> ...]
#   e.g. ./validate_ablations.sh ../results/ablation/context
#
# env:   EXAMS=../exams   (tasks are read from $EXAMS/tasks/$BENCH)
#        BENCH=mbpp
#
# Handles both layouts:
#   <dir>/<model>/<task_id>.json     (written by run_ablation.sh)
#   <dir>/<model>_<task_id>.json     (written by the old run_all.sh)
# Validation output goes next to the results:
#   <dir>/<model>/validation/<task_id>.txt   (or <dir>/validation/<model>_<task_id>.txt)

EXAMS="${EXAMS:-../exams}"
BENCH="${BENCH:-swebench}"
TASKS="$EXAMS/tasks/$BENCH"

[ $# -gt 0 ] || { echo "usage: $0 <ablation_dir> [...]"; exit 1; }
[ -d "$TASKS" ] || { echo "error: tasks directory '$TASKS' does not exist (cwd: $PWD)"; exit 1; }

find_task() {  # $1 = task id -> prints path or nothing
  if   [ -e "$TASKS/$1" ];      then echo "$TASKS/$1"
  elif [ -e "$TASKS/$1.json" ]; then echo "$TASKS/$1.json"
  fi
}

validate() {  # $1 = result file, $2 = task id, $3 = label, $4 = output txt
  local task; task=$(find_task "$2")
  if [ -z "$task" ]; then echo "[$3] task '$2' not found in $TASKS, skipped" >&2; return; fi
  mkdir -p "$(dirname "$4")"
  echo "=== [$3] ==="
  uv run python -m moulinette validate "$BENCH" "$task" "$1" 2>&1 | tee "$4"
}

for dir in "$@"; do
  dir="${dir%/}"
  [ -d "$dir" ] || { echo "error: '$dir' is not a directory" >&2; continue; }

  # nested layout: <dir>/<model>/<id>.json
  for res in "$dir"/*/*.json; do
    [ -e "$res" ] || continue
    model_dir=$(dirname "$res")
    [ "$(basename "$model_dir")" = validation ] && continue
    id=$(basename "$res" .json)
    validate "$res" "$id" "$(basename "$model_dir") $id" "$model_dir/validation/$id.txt"
  done

  # flat layout: <dir>/<model>_<id>.json
  for res in "$dir"/*.json; do
    [ -e "$res" ] || continue
    name=$(basename "$res" .json); rest="$name"; id=""
    while [[ "$rest" == *_* ]]; do
      rest="${rest#*_}"
      if [ -n "$(find_task "$rest")" ]; then id="$rest"; break; fi
    done
    [ -n "$id" ] || { echo "[$name] task not found, skipped" >&2; continue; }
    validate "$res" "$id" "$name" "$dir/validation/$name.txt"
  done
done
