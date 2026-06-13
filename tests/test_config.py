import os
import importlib
import pytest


def test_config_expoe_variaveis_obrigatorias():
    import config
    assert hasattr(config, "S3_ENDPOINT")
    assert hasattr(config, "S3_BUCKET")
    assert hasattr(config, "S3_ACCESS_KEY")
    assert hasattr(config, "S3_SECRET_KEY")
    assert hasattr(config, "RUNPOD_API_KEY")
    assert hasattr(config, "RUNPOD_ENDPOINT_ID")
    assert hasattr(config, "ANTHROPIC_API_KEY")
    assert hasattr(config, "N8N_WEBHOOK_URL")
    assert hasattr(config, "FLASK_PORT")
    assert hasattr(config, "MAX_CONCURRENT_JOBS")
    assert hasattr(config, "JOB_DIR")
    assert hasattr(config, "CLAUDE_MODEL")


def test_flask_port_e_inteiro():
    import config
    assert isinstance(config.FLASK_PORT, int)


def test_max_concurrent_jobs_e_inteiro():
    import config
    assert isinstance(config.MAX_CONCURRENT_JOBS, int)
    assert config.MAX_CONCURRENT_JOBS >= 1
