"""The optional config.toml next to the scripts: which sources run, limits, the schedule."""
import re
import tomllib

DEFAULTS = {
    "time": "08:00",
    "hacker_news": True,
    "daily_papers": True,
    "alphasignal": False,
    "telegram": False,
    "telegram_channels": ["neuraldvig", "whackdoor"],
    "hacker_news_max": 12,
    "daily_papers_max": 8,
    "max_stories_per_day": 25,
    "browser_fallback": False,
    "claude_ai_page": False,
    "python": "",
    "claude": "",
    "task_name": "AI Morning Wire",
}
SOURCES_KEYS = (
    "hacker_news",
    "daily_papers",
    "alphasignal",
    "telegram",
    "telegram_channels",
    "hacker_news_max",
    "daily_papers_max",
)
AT_LEAST_ONE_KEYS = ("max_stories_per_day", "hacker_news_max", "daily_papers_max")
TIME_OF_DAY = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class ConfigError(Exception):
    """config.toml is unreadable or has a bad key. The message names the key."""


def check_value(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"config.toml: '{key}' must be true or false")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"config.toml: '{key}' must be a whole number")
    elif isinstance(default, list):
        if not isinstance(value, list):
            raise ConfigError(f"config.toml: '{key}' must be a list of strings")
        for entry in value:
            if not isinstance(entry, str):
                raise ConfigError(f"config.toml: '{key}' must be a list of strings")
    elif not isinstance(value, str):
        raise ConfigError(f"config.toml: '{key}' must be a string")
    if key in AT_LEAST_ONE_KEYS and value < 1:
        raise ConfigError(f"config.toml: '{key}' must be 1 or more, not {value}")
    if key == "time" and not TIME_OF_DAY.fullmatch(value):
        raise ConfigError(f"config.toml: 'time' must look like \"08:00\" (24-hour HH:MM), not {value!r}")


def apply_table(settings, table, allowed_keys, where):
    for key, value in table.items():
        if key not in allowed_keys:
            raise ConfigError(f"config.toml: unknown key '{key}' {where}")
        check_value(key, value)
        settings[key] = value


def load_config(root):
    settings = dict(DEFAULTS)
    settings["telegram_channels"] = list(DEFAULTS["telegram_channels"])
    path = root / "config.toml"
    if not path.exists():
        return settings
    try:
        with open(path, "rb") as config_file:
            parsed = tomllib.load(config_file)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"config.toml is not valid TOML: {error}") from error
    except OSError as error:
        raise ConfigError(f"config.toml could not be read: {error}") from error
    sources = parsed.pop("sources", {})
    if not isinstance(sources, dict):
        raise ConfigError("config.toml: 'sources' must be a table")
    top_level_keys = []
    for key in DEFAULTS:
        if key not in SOURCES_KEYS:
            top_level_keys.append(key)
    apply_table(settings, parsed, top_level_keys, "at the top level")
    apply_table(settings, sources, SOURCES_KEYS, "in [sources]")
    if settings["telegram"] and len(settings["telegram_channels"]) == 0:
        raise ConfigError("config.toml: telegram is on but telegram_channels is empty")
    return settings
