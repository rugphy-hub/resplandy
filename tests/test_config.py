import pytest

from resplandy.config import ConfigError, family, load_config

FAKE_KEY = "FAKE-KEY-FOR-TESTS"


def test_config_loads_with_the_spec_values(cfg):
    assert cfg.verification.mode == "calibrate"
    assert cfg.limits.max_llm_calls_per_run == 40
    assert cfg.limits.max_cost_usd_per_run == 1.0
    assert cfg.limits.repair_rounds == 1
    assert cfg.models.fallbacks.get("judge", []) == []
    assert family(cfg.models.writer) != family(cfg.models.judge)
    assert cfg.replay is False


def test_missing_key_gives_a_clear_message(cfg):
    with pytest.raises(ConfigError) as err:
        cfg.secrets.require("OPENROUTER_API_KEY")
    assert "OPENROUTER_API_KEY" in str(err.value) and ".env" in str(err.value)


def test_keys_come_from_the_environment_and_are_never_shown(raw, write_config):
    cfg = load_config(write_config(raw), env={"OPENROUTER_API_KEY": FAKE_KEY, "REPLAY": "1"})
    assert cfg.secrets.require("OPENROUTER_API_KEY") == FAKE_KEY
    assert cfg.replay is True
    assert FAKE_KEY not in repr(cfg) + str(cfg) + repr(cfg.secrets) + cfg.model_dump_json()


def test_judge_fallback_list_is_refused(raw, write_config):
    raw["models"]["fallbacks"]["judge"] = ["fake/fallback-model"]
    with pytest.raises(ConfigError, match="judge"):
        load_config(write_config(raw), env={})


def test_writer_and_judge_must_be_different_families(raw, write_config):
    raw["models"]["judge"] = raw["models"]["writer"]
    with pytest.raises(ConfigError, match="famil"):
        load_config(write_config(raw), env={})


def test_a_fallback_may_not_cross_into_the_other_family(raw, write_config):
    raw["models"]["fallbacks"]["writer"] = [raw["models"]["judge"]]
    with pytest.raises(ConfigError, match="famil"):
        load_config(write_config(raw), env={})


def test_an_unknown_key_is_refused(raw, write_config):
    raw["limits"]["max_llm_calls"] = 99
    with pytest.raises(ConfigError):
        load_config(write_config(raw), env={})
