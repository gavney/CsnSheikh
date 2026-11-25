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
    elif env_path.exists():
        load_dotenv(env_path)

    if config_path is None:
        config_path = Path("config.yaml")

    data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Extract secrets / overrides from env
    proxy_val = os.getenv("SOCKS5_PROXY") or os.getenv("SOCKS_PROXY") or os.getenv("CASINO_PROXY")
    if not proxy_val:
        p_host = os.getenv("SOCKS5_HOST") or os.getenv("PROXY_HOST")
        if p_host:
            from urllib.parse import quote
            p_port = os.getenv("SOCKS5_PORT") or os.getenv("PROXY_PORT") or "1080"
            p_user = os.getenv("SOCKS5_USER") or os.getenv("PROXY_USER") or os.getenv("SOCKS5_USERNAME")
            p_pass = os.getenv("SOCKS5_PASSWORD") or os.getenv("PROXY_PASSWORD") or os.getenv("SOCKS5_PASS")
            if p_user and p_pass:
                proxy_val = f"socks5://{quote(p_user)}:{quote(p_pass)}@{p_host}:{p_port}"
            elif p_user:
                proxy_val = f"socks5://{quote(p_user)}@{p_host}:{p_port}"
            else:
                proxy_val = f"socks5://{p_host}:{p_port}"

    env_overrides = {
        "socks5_proxy": proxy_val,
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "telegram_admin_chat_id": os.getenv("TELEGRAM_ADMIN_CHAT_ID"),
        "bot_admin_password": os.getenv("BOT_ADMIN_PASSWORD"),
        "pragmatic_jsessionid": os.getenv("PRAGMATIC_JSESSIONID"),
        "pragmatic_socket_server": os.getenv("PRAGMATIC_SOCKET_SERVER"),
        "pragmatic_client_origin": os.getenv("PRAGMATIC_CLIENT_ORIGIN"),
        "log_level": os.getenv("LOG_LEVEL", data.get("log_level", "INFO")),
    }

    # Merge non-null env variables into config data
    for k, v in env_overrides.items():
        if v:
            data[k] = v

    # Propagate socks5_proxy to connection and session if not explicitly set
    app_proxy = data.get("socks5_proxy")
    if app_proxy:
        conn = data.setdefault("connection", {})
        if not conn.get("proxy"):
            conn["proxy"] = app_proxy
        sess = data.setdefault("session", {})
        if not sess.get("proxy"):
            sess["proxy"] = app_proxy

    return AppConfig.model_validate(data)


def save_config(config: AppConfig, config_path: Optional[Path] = None) -> None:
    """Save configuration to YAML file, omitting dynamic secrets."""
    if config_path is None:
        config_path = Path("config.yaml")

    data = {
        "tables": [
            {
                "id": t.id,
                "name": t.name,
                "url": t.url,
                "enabled": t.enabled,
            }
            for t in config.tables
        ],
        "connection": {
            "heartbeat_interval_sec": config.connection.heartbeat_interval_sec,
            "watchdog_timeout_sec": config.connection.watchdog_timeout_sec,
            "reconnect_min_delay_sec": config.connection.reconnect_min_delay_sec,
            "reconnect_max_delay_sec": config.connection.reconnect_max_delay_sec,
            "reconnect_jitter_sec": config.connection.reconnect_jitter_sec,
            "stagger_delay_sec": config.connection.stagger_delay_sec,
            "max_reconnect_attempts": config.connection.max_reconnect_attempts,
            **({"proxy": config.connection.proxy} if config.connection.proxy else {}),
        },
        "session": {
            "provider": config.session.provider,
            "browser_profile_dir": str(config.session.browser_profile_dir),
            "auto_refresh_on_auth_error": config.session.auto_refresh_on_auth_error,
            "proactive_refresh_interval_sec": config.session.proactive_refresh_interval_sec,
            **({"proxy": config.session.proxy} if config.session.proxy else {}),
        },
        "storage": {
            "database_path": str(config.storage.database_path),
            "window_size": config.storage.window_size,
        },
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "enabled": r.enabled,
                "tables": r.tables,
                "perspective": r.perspective,
                "tie_policy": r.tie_policy,
                "reset_on_shoe": r.reset_on_shoe,
                "condition": r.condition,
                "levels": [{"at": lvl.at, "actions": lvl.actions} for lvl in r.levels],
                "rearm": r.rearm,
                "notify_on_end": r.notify_on_end,
            }
            for r in config.rules
        ],
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
