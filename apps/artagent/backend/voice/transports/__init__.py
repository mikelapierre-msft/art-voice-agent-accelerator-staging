"""
Voice Transport Adapters
========================

Builtin transport adapters for ACS and Browser connections.

These adapters implement the TransportAdapter protocol and handle
wire protocol specifics, normalizing messages to a common format.
"""

from .acs import ACSTransportAdapter
from .browser import BrowserTransportAdapter
from .codecs import (
    AudioConverter,
    pcm16_to_ulaw,
    resample_16k_to_48k,
    resample_16k_to_8k,
    resample_48k_to_16k,
    resample_8k_to_16k,
    resample_linear,
    ulaw_to_pcm16,
)

__all__ = [
    # Adapters
    "ACSTransportAdapter",
    "BrowserTransportAdapter",
    # Codecs
    "AudioConverter",
    "ulaw_to_pcm16",
    "pcm16_to_ulaw",
    "resample_linear",
    "resample_8k_to_16k",
    "resample_16k_to_8k",
    "resample_48k_to_16k",
    "resample_16k_to_48k",
]
