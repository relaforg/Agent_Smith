#!/usr/bin/env bash
# Validates every solution under results/model_tests/<model>/{mbpp,swebench}/.
# Run from the repo root (the folder containing moulinette/ and results/).
#
# usage: ./validate_all.sh [model_dir ...]        e.g. ./validate_all.sh codestral qwen-3.8
# env:   TASKS=exams/tasks     tasks are read from $TASKS/<bench>/<task_id>[.json]
#        RESULTS=results/model_tests
#        FORCE=1               re-validate even if a validation file already exists
#
# Output: results/model_tests/<model>/<bench>_validation/<task_id>.txt

ROOT="$PWD"
RESULTS="$(cd "${RESULTS:-results/model_tests}" 2>/dev/null && pwd)" || { echo "error: results dir not found (cwd: $ROOT)"; exit 1; }
TASKS="$(cd "${TASKS:-exams/tasks}" 2>/dev/null && pwd)" || { echo "error: tasks dir not found (set TASKS=...; cwd: $ROOT)"; exit 1; }
[ -d "$ROOT/moulinette" ] || { echo "error: $ROOT/moulinette not found, run from the repo root"; exit 1; }

models=("$@")
if [ ${#models[@]} -eq 0 ]; then
  for d in "$RESULTS"/*/; do models+=("$(basename "$d")"); done
fi

done_n=0; skip_n=0; miss_n=0

for model in "${models[@]}"; do
  for bench in mbpp swebench; do
    res_dir="$RESULTS/$model/$bench"
    [ -d "$res_dir" ] || continue
    val_dir="$RESULTS/$model/${bench}_validation"

    for res in "$res_dir"/*.json; do
      [ -e "$res" ] || continue
      name=$(basename "$res" .json)
      id="${name##*_}"                      # codestral_task10 -> task10

      if   [ -e "$TASKS/$bench/$id.json" ]; then task="$TASKS/$bench/$id.json"
      elif [ -e "$TASKS/$bench/$id" ];      then task="$TASKS/$bench/$id"
      else echo "[$model] $bench $id: task file not found, skipped" >&2; miss_n=$((miss_n+1)); continue
      fi

      out="$val_dir/$id.txt"
      if [ -e "$out" ] && [ -z "$FORCE" ]; then
        echo "[$model] $bench $id: already validated (FORCE=1 to redo)"; skip_n=$((skip_n+1)); continue
      fi

      mkdir -p "$val_dir"
      echo "=== [$model] $bench $id ==="
      ( cd "$ROOT/moulinette" && uv run moulinette_eval validate "$bench" "$task" "$res" ) 2>&1 | tee "$out"
      done_n=$((done_n+1))
    done
  done
done

echo
echo "validated: $done_n | already done: $skip_n | task not found: $miss_n"
