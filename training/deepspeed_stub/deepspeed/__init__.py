"""Minimal import shim for CosyVoice's torch_ddp training path.

CosyVoice imports DeepSpeed unconditionally even when train_engine=torch_ddp.
The functions below fail clearly if a DeepSpeed code path is selected.
"""

from __future__ import annotations


def add_config_arguments(parser):
    return parser


def init_distributed(*_args, **_kwargs):
    raise RuntimeError("DeepSpeed is unavailable; use --train_engine torch_ddp")


def initialize(*_args, **_kwargs):
    raise RuntimeError("DeepSpeed is unavailable; use --train_engine torch_ddp")
