#!/usr/bin/env bash
# usage: source .venv/bin/activate && ./run_exams.sh

MBPP_TASKS=exams/tasks/mbpp          # dir of mbpp task .json files
SWE_TASKS=exams/tasks/swebench       # dir of swebench task .json files
N=10
OUT=exams/results

MODELS=(gpt-oss-120b gpt-oss-20b qwen-3.8-27b qwen-3.6-27b gemma-31 gemma-26 qwen nemotron codestral ministral)

export PYTHONPATH="$PWD/src:$PYTHONPATH"

# same random tasks for every model
mapfile -t MBPP < <(shuf -n "$N" -e "$MBPP_TASKS"/*.json)
mapfile -t SWE  < <(shuf -n "$N" -e "$SWE_TASKS"/*.json)

for model in "${MODELS[@]}"; do
  mkdir -p "$OUT/$model/mbpp" "$OUT/$model/swebench"

  for t in "${MBPP[@]}"; do
    id=$(basename "$t" .json)
    echo "[$model] mbpp $id"
    uv run -m agent_mbpp --task-file "$t" --output "$OUT/$model/mbpp/$id.json" \
      --model-name "$model" > "$OUT/$model/mbpp/$id.log" 2>&1
  done

  for t in "${SWE[@]}"; do
    id=$(basename "$t" .json)
    echo "[$model] swebench $id"
    uv run -m agent_swebench --task-file "$t" --output "$OUT/$model/swebench/$id.json" \
      --model-name "$model" > "$OUT/$model/swebench/$id.log" 2>&1
  done
done
