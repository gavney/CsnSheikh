"""Message formatting and templating for Telegram and Console alerts."""

from typing import Any, Dict, List, Optional
from src.notifier.base import Priority


OUTCOME_EMOJI = {
    "P": "🔵",
    "PLAYER": "🔵",
    "B": "🔴",
    "BANKER": "🔴",
    "T": "🟢",
    "TIE": "🟢",
}


class AlertFormatter:
    """Formats structured alert models into human-readable Markdown messages."""

    @staticmethod
    def format_outcomes_emoji(outcomes: List[str]) -> str:
        """Convert list of outcome characters into visual emoji sequence."""
        return "".join(OUTCOME_EMOJI.get(o.upper(), "⚪") for o in outcomes)

    @classmethod
    def format_rule_alert(
        cls,
        table_name: str,
        table_id: str,
        rule_id: str,
        level: Optional[int],
        metric: float,
        round_id: str,
        priority: Priority,
        details: Dict[str, Any],
        is_end: bool = False,
        table_url: Optional[str] = None,
    ) -> str:
        """Format a game rule alert with visual cues and metadata."""
        target_of = str(details.get("of", "player")).lower()
        persp = str(details.get("perspective", "player")).lower()

        if target_of in ("player", "p"):
            who = "Игрок"
        elif target_of in ("banker", "b"):
            who = "Банкир"
        elif target_of in ("tie", "t"):
            who = "Ничья"
        elif target_of in ("win", "victory"):
            who = "Игрок" if persp == "player" else "Банкир"
        elif target_of in ("loss", "lose"):
            who = "Банкир" if persp == "player" else "Игрок"
        else:
            who = target_of.capitalize()

        table_str = f"[{table_name}]({table_url})" if table_url else f"{table_name} (`{table_id}`)"

        if is_end:
            final_len = int(details.get("final_metric", metric))
            break_char = details.get("break_outcome", "")
            emoji_break = OUTCOME_EMOJI.get(break_char, "")
            lines = [
                "ℹ️ *СЕРИЯ ЗАВЕРШЕНА*",
                f"📊 *Стол*: {table_str}",
                f"📈 Итого: {who} {final_len} подряд",
            ]
            if emoji_break:
                lines.append(f"🏁 Прервана: {emoji_break} {break_char}")
            return "\n".join(lines)

        streak_val = int(details.get("streak", metric))
        recent_outcomes = details.get("last_outcomes", [])
        emoji_seq = cls.format_outcomes_emoji(recent_outcomes)

        header_icon = "🚨 *СРОЧНОЕ ОПОВЕЩЕНИЕ*" if priority == Priority.URGENT else "⚠️ *ВНИМАНИЕ: СЕРИЯ*"
        lvl_tag = f" [Порог {level}]" if level else ""

        lines = [
            f"{header_icon}{lvl_tag}",
            f"📊 *Стол*: {table_str}",
            f"🔥 {who} {streak_val} подряд",
        ]
        if emoji_seq:
            lines.append(f"🎞 *Дорожка*: {emoji_seq}")

        return "\n".join(lines)

    @staticmethod
    def format_system_alert(level: str, text: str) -> str:
        """Format a system health, watchdog, startup or auth message."""
        lvl = level.upper()
        if lvl in ("STARTUP", "START"):
            title = "🚀 *СЕРВИС ЗАПУЩЕН*"
        elif lvl in ("AUTH_EXPIRED", "EXPIRED", "AUTH"):
            title = "⚠️ *ТОКЕН СЕССИИ ИСТЕК*"
        elif lvl in ("SESSION_REFRESH", "REFRESH"):
            title = "🔄 *ПРОАКТИВНОЕ ОБНОВЛЕНИЕ СЕССИИ*"
        elif lvl == "HEARTBEAT":
            title = "💓 *СИСТЕМНЫЙ ПУЛЬС*"
        elif lvl in ("ERROR", "CRITICAL"):
            title = "🚨 *СИСТЕМНАЯ ОШИБКА*"
        elif lvl == "WARNING":
            title = "⚠️ *ПРЕДУПРЕЖДЕНИЕ*"
        else:
            title = f"🛠️ *СИСТЕМНОЕ СООБЩЕНИЕ* [{lvl}]"

        return f"{title}\n{text}"
