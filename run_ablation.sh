#!/usr/bin/env bash
# Runs codestral, gpt-oss-120b and gemma-31 on every task of a directory.
#
# usage: ./run_ablation.sh <tasks_dir> [output_dir]
# e.g.   ./run_ablation.sh exams/tasks/mbpp exams/ablation
# env:   AGENT=agent_mbpp   MODELS="codestral gpt-oss-120b gemma-31"
#
# Results:  <output_dir>/<model>/<task_id>.json   (+ .log next to it)
# Safe to stop and restart: finished tasks are skipped, nothing is overwritten.

tasks="$1"; out_dir="${2:-exams/ablation}"
[ -d "$tasks" ] || { echo "usage: $0 <tasks_dir> [output_dir]"; exit 1; }

AGENT="${AGENT:-agent_swebench}"
MODELS="${MODELS:-codestral gpt-oss-120b gemma-31}"
export PYTHONPATH="$PWD/src:$PYTHONPATH"

total=$(ls "$tasks"/*.json 2>/dev/null | wc -l)
[ "$total" -gt 0 ] || { echo "no .json tasks in $tasks"; exit 1; }

for model in $MODELS; do
  mkdir -p "$out_dir/$model"
  n=0
  for task in "$tasks"/*.json; do
    n=$((n + 1))
    id=$(basename "$task" .json)
    out="$out_dir/$model/$id.json"

    if [ -e "$out" ]; then
      echo "[$model] $n/$total $id: skipped (done)"
      continue
    fi

    echo "[$model] $n/$total $id: running..."
    if uv run -m "$AGENT" --task-file "$task" --output "$out" \
         --model-name "$model" > "$out_dir/$model/$id.log" 2>&1 && [ -e "$out" ]; then
      echo "[$model] $n/$total $id: done"
    else
      echo "[$model] $n/$total $id: CRASHED (see $out_dir/$model/$id.log)"
      tail -n 3 "$out_dir/$model/$id.log" | sed 's/^/    | /'
    fi
  done
done

echo "finished. results in $out_dir/"
