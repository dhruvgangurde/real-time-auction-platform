"""Demo request schemas (DC-11)."""

from pydantic import BaseModel, ConfigDict


class StressStart(BaseModel):
    """Starting "Try to break it" takes no input: bots, bids and duration are the server's
    bounded settings. Declared so a body with any field is refused, not silently ignored."""

    model_config = ConfigDict(extra="forbid")
