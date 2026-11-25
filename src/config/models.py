"""Configuration models using Pydantic v2."""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class TableConfig(BaseModel):
    """Configuration for a single live table."""
    id: str
    name: str
    enabled: bool = True
    url: Optional[str] = None


class ConnectionConfig(BaseModel):
    """WebSocket connection, heartbeat and watchdog parameters."""
    heartbeat_interval_sec: float = 10.0
    watchdog_timeout_sec: float = 15.0
    reconnect_min_delay_sec: float = 1.0
    reconnect_max_delay_sec: float = 60.0
    reconnect_jitter_sec: float = 0.5
    stagger_delay_sec: float = 2.0
    max_reconnect_attempts: int = 10
    system_heartbeat_interval_sec: float = 3600.0
    round_staleness_threshold_sec: float = 300.0
    proxy: Optional[str] = None


class SessionConfig(BaseModel):
    """Session provider parameters."""
    provider: Literal["env", "playwright"] = "env"
    browser_profile_dir: Path = Path("./browser_profile")
    auto_refresh_on_auth_error: bool = True
    headless: bool = True
    proactive_refresh_interval_sec: float = 1800.0
    proxy: Optional[str] = None


class StorageConfig(BaseModel):
    """Local SQLite storage settings."""
    database_path: Path = Path("./casino_monitor.db")
    window_size: int = 200


class RuleLevelConfig(BaseModel):
    """Escalation level configuration."""
    at: int
    actions: List[str]


class RuleConfig(BaseModel):
    """Declarative monitoring rule."""
    id: str
    name: Optional[str] = None
    enabled: bool = True
    tables: Union[Literal["all"], List[str]] = "all"
    perspective: Literal["player", "banker"] = "player"
    tie_policy: Literal["ignore", "break", "count_as_loss"] = "ignore"
    reset_on_shoe: bool = True
    condition: Dict[str, Any]
    levels: List[RuleLevelConfig] = Field(default_factory=list)
    rearm: Literal["on_break", "always"] = "on_break"
    notify_on_end: bool = False
    cooldown_rounds: int = 0
    repeat_every: int = 0


class AppConfig(BaseModel):
    """Complete application configuration."""
    tables: List[TableConfig] = Field(default_factory=list)
    connection: ConnectionConfig = Field(default_factory=ConnectionConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    rules: List[RuleConfig] = Field(default_factory=list)

    # Environment variables overrides (secrets)
    socks5_proxy: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_admin_chat_id: Optional[str] = None
    bot_admin_password: str = "CasinoBoss2026!Strict#Admin"

    pragmatic_jsessionid: Optional[str] = None
    pragmatic_socket_server: Optional[str] = None
    pragmatic_client_origin: Optional[str] = None

    log_level: str = "INFO"
