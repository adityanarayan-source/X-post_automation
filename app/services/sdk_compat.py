"""Tiny helpers that make our use of the `xdk` SDK tolerant to minor signature changes."""
from __future__ import annotations

import inspect
from typing import Any, Callable


def call_flexible(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Call `fn`, passing only the keyword arguments its signature accepts."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    return fn(*args, **{k: v for k, v in kwargs.items() if k in params})


def get_field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` from a dict or from an object/Pydantic model attribute."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
