#!/usr/bin/env bash
# usage: ./run_all.sh <model> <tasks_dir> [output_dir]
# e.g.   ./run_all.sh codestral exams/tasks/mbpp exams/ablation

model="$1"; tasks="$2"; out_dir="$3"
[ -n "$model" ] && [ -d "$tasks" ] || { echo "usage: $0 <model> <tasks_dir> [output_dir]"; exit 1; }

mkdir -p "$out_dir"
export PYTHONPATH="$PWD/src:$PYTHONPATH"

for task in "$tasks"/*.json; do
  id=$(basename "$task" .json)
  out="$out_dir/${model}_$id.json"

  if [ -e "$out" ]; then
    echo "[$id] skipped (already done)"
    continue
  fi

  echo "[$id] running..."
  uv run -m agent_mbpp --task-file "$task" --output "$out" \
    --model-name "$model" > "$out_dir/${model}_$id.log" 2>&1 \
    || echo "[$id] CRASHED, see $out_dir/${model}_$id.log"
done
