import shutil
from pathlib import Path

import pytest

from ainews import config

REPO_ROOT = Path(__file__).resolve().parent.parent


def write_config(folder, text):
    (folder / "config.toml").write_text(text, encoding="utf-8")


def test_load_config_without_a_file_returns_the_public_defaults(tmp_path):
    assert config.load_config(tmp_path) == config.DEFAULTS
    assert config.DEFAULTS["hacker_news_max"] == 12
    assert config.DEFAULTS["daily_papers_max"] == 8
    assert config.DEFAULTS["alphasignal"] is False
    assert config.DEFAULTS["browser_fallback"] is False
    assert config.DEFAULTS["time"] == "08:00"


def test_load_config_overrides_only_the_keys_it_sets(tmp_path):
    write_config(tmp_path, 'time = "07:30"\n\n[sources]\nalphasignal = true\n')
    loaded = config.load_config(tmp_path)
    expected = dict(config.DEFAULTS)
    expected["time"] = "07:30"
    expected["alphasignal"] = True
    assert loaded == expected


def test_load_config_returns_a_copy_of_the_defaults(tmp_path):
    loaded = config.load_config(tmp_path)
    loaded["telegram_channels"].append("someone")
    assert "someone" not in config.DEFAULTS["telegram_channels"]


def test_load_config_rejects_an_unknown_key(tmp_path):
    write_config(tmp_path, "[sources]\nhackernews = true\n")
    with pytest.raises(config.ConfigError, match="hackernews"):
        config.load_config(tmp_path)


def test_load_config_rejects_a_source_key_at_the_top_level(tmp_path):
    write_config(tmp_path, "hacker_news = true\n")
    with pytest.raises(config.ConfigError, match="hacker_news"):
        config.load_config(tmp_path)


def test_load_config_rejects_a_top_level_key_inside_sources(tmp_path):
    write_config(tmp_path, '[sources]\ntime = "07:30"\n')
    with pytest.raises(config.ConfigError, match="time"):
        config.load_config(tmp_path)


@pytest.mark.parametrize("bad_time", ["8:00", "25:00", "08:60", "0800"])
def test_load_config_rejects_a_bad_time(tmp_path, bad_time):
    write_config(tmp_path, f'time = "{bad_time}"\n')
    with pytest.raises(config.ConfigError, match="time"):
        config.load_config(tmp_path)


def test_load_config_rejects_a_wrong_type(tmp_path):
    write_config(tmp_path, '[sources]\nhacker_news_max = "12"\n')
    with pytest.raises(config.ConfigError, match="hacker_news_max"):
        config.load_config(tmp_path)


def test_load_config_does_not_accept_a_bool_for_an_int(tmp_path):
    write_config(tmp_path, "max_stories_per_day = true\n")
    with pytest.raises(config.ConfigError, match="max_stories_per_day"):
        config.load_config(tmp_path)


def test_load_config_rejects_channels_that_are_not_a_list_of_strings(tmp_path):
    write_config(tmp_path, "[sources]\ntelegram_channels = [1, 2]\n")
    with pytest.raises(config.ConfigError, match="telegram_channels"):
        config.load_config(tmp_path)
    write_config(tmp_path, '[sources]\ntelegram_channels = "neuraldvig"\n')
    with pytest.raises(config.ConfigError, match="telegram_channels"):
        config.load_config(tmp_path)


def test_load_config_reports_broken_toml_as_config_error(tmp_path):
    write_config(tmp_path, "time = = nonsense\n")
    with pytest.raises(config.ConfigError, match="config.toml"):
        config.load_config(tmp_path)


def test_config_example_matches_the_defaults(tmp_path):
    shutil.copy(REPO_ROOT / "config.example.toml", tmp_path / "config.toml")
    assert config.load_config(tmp_path) == config.DEFAULTS


@pytest.mark.parametrize("key", ["max_stories_per_day", "hacker_news_max", "daily_papers_max"])
@pytest.mark.parametrize("bad_value", [0, -3])
def test_load_config_rejects_a_count_below_one(tmp_path, key, bad_value):
    if key == "max_stories_per_day":
        write_config(tmp_path, f"{key} = {bad_value}\n")
    else:
        write_config(tmp_path, f"[sources]\n{key} = {bad_value}\n")
    with pytest.raises(config.ConfigError, match=key):
        config.load_config(tmp_path)


def test_load_config_accepts_a_count_of_one(tmp_path):
    write_config(tmp_path, "max_stories_per_day = 1\n[sources]\nhacker_news_max = 1\ndaily_papers_max = 1\n")
    assert config.load_config(tmp_path)["daily_papers_max"] == 1


def test_load_config_rejects_telegram_on_with_no_channels(tmp_path):
    write_config(tmp_path, "[sources]\ntelegram = true\ntelegram_channels = []\n")
    with pytest.raises(config.ConfigError, match="telegram is on but telegram_channels is empty"):
        config.load_config(tmp_path)


def test_load_config_accepts_no_channels_while_telegram_is_off(tmp_path):
    write_config(tmp_path, "[sources]\ntelegram_channels = []\n")
    assert config.load_config(tmp_path)["telegram_channels"] == []


def test_load_config_reports_an_unreadable_file_as_config_error(tmp_path):
    (tmp_path / "config.toml").mkdir()
    with pytest.raises(config.ConfigError, match="could not be read"):
        config.load_config(tmp_path)
