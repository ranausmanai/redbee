"""Load user config from config.json (same directory as this file)."""
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.json"
_config = None


def load_config():
    global _config
    if _config is None:
        if not _CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"Config not found: {_CONFIG_PATH}\n"
                "Copy config.example.json to config.json and fill in your details."
            )
        _config = json.loads(_CONFIG_PATH.read_text())
    return _config


def get_profile():
    return load_config()["profile"]


def get_resume_path():
    return Path(load_config()["resume_path"]).expanduser()


def get_applicant_summary():
    return load_config()["applicant_summary"]


def get_twitter_handle():
    return load_config()["twitter_handle"]


def get_github_username():
    return load_config()["github_username"]


def get_repos_to_promote():
    return load_config()["repos_to_promote"]
