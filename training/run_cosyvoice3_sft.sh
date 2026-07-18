#!/usr/bin/env bash
# Prepare features and fine-tune the Fun-CosyVoice3 LLM on one GPU.
set -euo pipefail

DATASET="${1:?usage: run_cosyvoice3_sft.sh <dataset-dir> <experiment> [epochs]}"
EXP="${2:?usage: run_cosyvoice3_sft.sh <dataset-dir> <experiment> [epochs]}"
EPOCHS="${3:-10}"
STAGE="${COSYVOICE_STAGE:-all}"

COSYVOICE_DIR="${COSYVOICE_DIR:-/opt/tts/CosyVoice}"
COSYVOICE_PYTHON="${COSYVOICE_PYTHON:-/opt/tts/cosyvoice3_venv/bin/python}"
PRETRAINED_DIR="${PRETRAINED_DIR:-$COSYVOICE_DIR/pretrained_models/Fun-CosyVoice3-0.5B}"
WORK_ROOT="${WORK_ROOT:-/opt/tts/cosyvoice3-sft}"
PUBLISHED_ROOT="${PUBLISHED_ROOT:-$COSYVOICE_DIR/pretrained_models}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$WORK_ROOT/$EXP"
MODEL_DIR="$WORK_DIR/exp/llm"
TB_DIR="$WORK_DIR/tensorboard/llm"
CONFIG="$WORK_DIR/cosyvoice3-sft.yaml"

for path in "$COSYVOICE_PYTHON" "$PRETRAINED_DIR/llm.pt" \
  "$PRETRAINED_DIR/campplus.onnx" "$PRETRAINED_DIR/speech_tokenizer_v3.onnx"; do
  [ -e "$path" ] || { echo "missing required file: $path" >&2; exit 1; }
done
for split in train dev; do
  for name in wav.scp text utt2spk spk2utt instruct; do
    [ -s "$DATASET/$split/$name" ] || {
      echo "missing dataset file: $DATASET/$split/$name" >&2
      exit 1
    }
  done
done

mkdir -p "$WORK_DIR/data" "$MODEL_DIR" "$TB_DIR"
cp -a "$DATASET/train" "$WORK_DIR/data/"
cp -a "$DATASET/dev" "$WORK_DIR/data/"

export PYTHONPATH="$SCRIPT_DIR/deepspeed_stub:$COSYVOICE_DIR:$COSYVOICE_DIR/third_party/Matcha-TTS${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

for split in train dev; do
  split_dir="$WORK_DIR/data/$split"
  if [ ! -s "$split_dir/utt2embedding.pt" ]; then
    "$COSYVOICE_PYTHON" "$COSYVOICE_DIR/tools/extract_embedding.py" \
      --dir "$split_dir" --onnx_path "$PRETRAINED_DIR/campplus.onnx"
  fi
  if [ ! -s "$split_dir/utt2speech_token.pt" ]; then
    "$COSYVOICE_PYTHON" "$COSYVOICE_DIR/tools/extract_speech_token.py" \
      --dir "$split_dir" --onnx_path "$PRETRAINED_DIR/speech_tokenizer_v3.onnx"
  fi
  if [ ! -s "$split_dir/parquet/data.list" ]; then
    mkdir -p "$split_dir/parquet"
    "$COSYVOICE_PYTHON" "$COSYVOICE_DIR/tools/make_parquet_list.py" \
      --num_utts_per_parquet 1000 --num_processes 1 \
      --src_dir "$split_dir" --des_dir "$split_dir/parquet"
  fi
done

if [ "$STAGE" = "prepare" ]; then
  echo "CosyVoice3 data preparation complete: $WORK_DIR/data"
  exit 0
fi

"$COSYVOICE_PYTHON" "$SCRIPT_DIR/configure_cosyvoice3_sft.py" \
  --input "$COSYVOICE_DIR/examples/libritts/cosyvoice3/conf/cosyvoice3.yaml" \
  --output "$CONFIG" --epochs "$EPOCHS"

"$COSYVOICE_PYTHON" -m torch.distributed.run --standalone --nproc_per_node=1 \
  "$COSYVOICE_DIR/cosyvoice/bin/train.py" \
  --train_engine torch_ddp \
  --config "$CONFIG" \
  --train_data "$WORK_DIR/data/train/parquet/data.list" \
  --cv_data "$WORK_DIR/data/dev/parquet/data.list" \
  --qwen_pretrain_path "$PRETRAINED_DIR/CosyVoice-BlankEN" \
  --onnx_path "$PRETRAINED_DIR" \
  --model llm \
  --checkpoint "$PRETRAINED_DIR/llm.pt" \
  --model_dir "$MODEL_DIR" \
  --tensorboard_dir "$TB_DIR" \
  --ddp.dist_backend nccl \
  --num_workers 2 \
  --prefetch 20 \
  --pin_memory \
  --use_amp

"$COSYVOICE_PYTHON" "$COSYVOICE_DIR/cosyvoice/bin/average_model.py" \
  --dst_model "$MODEL_DIR/llm-sft.pt" \
  --src_path "$MODEL_DIR" \
  --num "$((EPOCHS < 5 ? EPOCHS : 5))" \
  --val_best

PUBLISHED_DIR="$PUBLISHED_ROOT/$EXP-CosyVoice3-SFT"
rm -rf "$PUBLISHED_DIR"
cp -al "$PRETRAINED_DIR" "$PUBLISHED_DIR"
rm -f "$PUBLISHED_DIR/llm.pt"
cp "$MODEL_DIR/llm-sft.pt" "$PUBLISHED_DIR/llm.pt"
printf '%s\n' \
  "experiment=$EXP" \
  "epochs=$EPOCHS" \
  "checkpoint=$MODEL_DIR/llm-sft.pt" \
  > "$PUBLISHED_DIR/ko-tts-sft.meta"

echo "CosyVoice3 LLM SFT complete: $PUBLISHED_DIR"
