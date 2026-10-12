from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def source(name: str) -> str:
    """Source of app/lua/<name>.lua. Callers register it with redis-py register_script,
    which runs EVALSHA and falls back to EVAL on NOSCRIPT."""
    return (_DIR / f"{name}.lua").read_text(encoding="utf-8")
