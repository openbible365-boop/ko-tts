"""Unused DeepSpeed helper imported by CosyVoice's torch_ddp utilities."""


def estimate_zero2_model_states_mem_needs_all_live(*_args, **_kwargs):
    raise RuntimeError("DeepSpeed is unavailable; use --train_engine torch_ddp")
