from app.routers.export import _training_exp, _voice_kind


def test_training_exp_separates_fine_tuning_engines():
    assert _training_exp("kr f2", "sovits") == "kr_f2-SV2"
    assert _training_exp("kr f2", "cosyvoice3") == "kr_f2-CV3"


def test_training_exp_keeps_subset_after_engine_suffix():
    assert _training_exp("kr-f2-SV2", "sovits", 50) == "kr-f2-SV2_50"
    assert _training_exp("kr-f2-CV3_50", "cosyvoice3") == "kr-f2-CV3"


def test_voice_kind_recognizes_standalone_model_types():
    assert _voice_kind("kr-f2-SV2") == "sovits"
    assert _voice_kind("kr-f2-SV2_50") == "sovits"
    assert _voice_kind("kr-f2-CV3") == "cosyvoice3_sft"
    assert _voice_kind("kr-f2-CV3_200") == "cosyvoice3_sft"
    assert _voice_kind("kr-f2-零样本") == "zero_shot"
    assert _voice_kind("legacy-name") == "legacy"
