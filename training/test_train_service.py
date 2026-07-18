from gpu_backend import train_service


def test_active_job_info_includes_job_id(monkeypatch):
    monkeypatch.setattr(train_service, "_active_job", "job-123")
    monkeypatch.setattr(
        train_service,
        "JOBS",
        {
            "job-123": {
                "exp": "voice-a",
                "trainer": "cosyvoice3",
                "status": "training",
            }
        },
    )

    assert train_service.active_job_info() == {
        "job_id": "job-123",
        "exp": "voice-a",
        "trainer": "cosyvoice3",
        "status": "training",
    }


def test_active_job_info_is_none_when_idle(monkeypatch):
    monkeypatch.setattr(train_service, "_active_job", None)
    assert train_service.active_job_info() is None
