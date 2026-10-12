import re
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent
# a line `--#include name` is replaced by app/lua/name.lua, so place_bid.lua and set_proxy.lua
# share one copy of the arbitration core while SCRIPT LOAD still sees a single script
_INCLUDE = re.compile(r"^--#include (\w+)[ \t]*$", re.MULTILINE)


@cache
def source(name: str) -> str:
    """Assembled source of app/lua/<name>.lua. Callers register it with redis-py
    register_script, which runs EVALSHA and falls back to EVAL on NOSCRIPT."""
    text = (_DIR / f"{name}.lua").read_text(encoding="utf-8")
    return _INCLUDE.sub(lambda m: source(m.group(1)), text)
