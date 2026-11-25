"""Plugin registry for condition evaluators."""

from typing import Callable, Dict, List, Type
from .base import Condition

_CONDITION_REGISTRY: Dict[str, Type[Condition]] = {}


def register_condition(name: str) -> Callable[[Type[Condition]], Type[Condition]]:
    """Decorator to register a Condition evaluator plugin by name."""
    def decorator(cls: Type[Condition]) -> Type[Condition]:
        _CONDITION_REGISTRY[name.lower()] = cls
        return cls
    return decorator


def get_condition(name: str) -> Type[Condition]:
    """Retrieve registered condition class."""
    key = name.lower()
    if key not in _CONDITION_REGISTRY:
        raise KeyError(
            f"Condition type '{name}' not found in registry. "
            f"Available conditions: {list(_CONDITION_REGISTRY.keys())}"
        )
    return _CONDITION_REGISTRY[key]


def list_conditions() -> List[str]:
    """List all registered condition types."""
    return sorted(list(_CONDITION_REGISTRY.keys()))
