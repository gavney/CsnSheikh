"""Configuration loader for YAML and .env settings."""

import os
from pathlib import Path
from typing import Optional
import yaml
from dotenv import load_dotenv

from .models import AppConfig


def load_config(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None
) -> AppConfig:
    """Load and validate configuration from YAML file and .env file.
    
    Args:
        config_path: Path to config.yaml. Defaults to ./config.yaml.
        env_path: Path to .env file. Defaults to ./.env.
    """
    # Load .env if present
    if env_path is None:
        env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    if config_path is None:
        config_path = Path("config.yaml")

    data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Extract secrets / overrides from env
    env_overrides = {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "telegram_admin_chat_id": os.getenv("TELEGRAM_ADMIN_CHAT_ID"),
        "pragmatic_jsessionid": os.getenv("PRAGMATIC_JSESSIONID"),
        "pragmatic_socket_server": os.getenv("PRAGMATIC_SOCKET_SERVER"),
        "pragmatic_client_origin": os.getenv("PRAGMATIC_CLIENT_ORIGIN"),
        "log_level": os.getenv("LOG_LEVEL", data.get("log_level", "INFO")),
    }

    # Merge non-null env variables into config data
    for k, v in env_overrides.items():
        if v:
            data[k] = v

    return AppConfig.model_validate(data)
