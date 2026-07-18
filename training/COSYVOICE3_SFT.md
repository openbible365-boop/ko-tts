# Fun-CosyVoice3 LLM fine-tuning

This directory contains the reproducible single-GPU fine-tuning path used by
the ko-tts project. It follows the official `examples/libritts/cosyvoice3`
pipeline and currently fine-tunes the LLM checkpoint only.

## Server layout

- CosyVoice source: `/opt/tts/CosyVoice`
- Python environment: `/opt/tts/cosyvoice3_venv`
- Base model: `/opt/tts/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B`
- Converted datasets: `/opt/tts/cosyvoice3-datasets/<experiment>`
- Training output: `/opt/tts/cosyvoice3-sft/<experiment>`

## 1. Convert a ko-tts dataset

```bash
/opt/tts/cosyvoice3_venv/bin/python \
  /opt/tts/CosyVoice/training_ko_tts/prepare_cosyvoice3_dataset.py \
  --train-list /www/yuyin/data/kr-f1/train.list \
  --data-root /www/yuyin/data/kr-f1 \
  --out /opt/tts/cosyvoice3-datasets/kr-f1 \
  --dev-ratio 0.15
```

The command creates deterministic `train` and `dev` splits containing
`wav.scp`, `text`, `utt2spk`, `spk2utt`, and `instruct`.

## 2. Prepare features without training

```bash
COSYVOICE_STAGE=prepare \
  /opt/tts/CosyVoice/training_ko_tts/run_cosyvoice3_sft.sh \
  /opt/tts/cosyvoice3-datasets/kr-f1 kr-f1 10
```

This extracts CAMPPlus speaker embeddings and CosyVoice3 speech tokens, then
builds parquet files.

## 3. Train

```bash
systemctl start yuyin-cosyvoice3-sft@kr-f1.service
journalctl -fu yuyin-cosyvoice3-sft@kr-f1.service
```

The systemd template stops the inference engines before training and
starts them again in `ExecStopPost`, including when training fails. The
business backend remains online.

The final averaged checkpoint is:

```text
/opt/tts/cosyvoice3-sft/kr-f1/exp/llm/llm-sft.pt
```

## 4. Serve a fine-tuned model

The `kr-f1` model is packaged at:

```text
/opt/tts/CosyVoice/pretrained_models/kr-f1-CosyVoice3-SFT
```

`yuyin-cosyvoice3-sft-engine.service` serves fine-tuned models on
`127.0.0.1:9884` and loads the requested experiment dynamically. The original
zero-shot model remains on `127.0.0.1:9883`.

In the web application, use **CosyVoice3 微调** on the review page. The
separate flow diagram reports dataset preparation, feature extraction, LLM
fine-tuning, checkpoint publication, and model availability. The voices page
can then synthesize the same text with CosyVoice3 zero-shot and the selected
fine-tuned model for direct comparison.

## First baseline

- Dataset: `kr-f1`
- Audio: 27 segments, about 5.05 minutes
- Split: 23 train / 4 dev
- Training: LLM, 10 epochs, learning rate `1e-5`
- Final model: validation-best average of 5 checkpoints
- Smoke test: Korean output, mono PCM WAV, 24 kHz

Five minutes is enough to validate the pipeline, but not enough to settle
model quality. Compare the zero-shot and fine-tuned results before increasing
epochs or adding data.
