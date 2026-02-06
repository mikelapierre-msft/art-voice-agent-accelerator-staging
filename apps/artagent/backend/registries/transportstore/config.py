"""
Transport Configuration
=======================

Configuration schema and utilities for transport adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransportConfig:
    """
    Configuration for a transport adapter.

    This is a base configuration that all transports share.
    Transport-specific config is passed via the `extra` field.

    Attributes:
        enabled: Whether this transport is enabled
        sample_rate_in: Expected input sample rate (Hz)
        sample_rate_out: Output sample rate for TTS (Hz)
        codec_in: Input audio codec ("pcm16", "ulaw", "alaw")
        codec_out: Output audio codec
        extra: Transport-specific configuration
    """

    enabled: bool = True
    sample_rate_in: int = 16000
    sample_rate_out: int = 16000
    codec_in: str = "pcm16"
    codec_out: str = "pcm16"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransportConfig:
        """
        Create config from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            TransportConfig instance
        """
        known_keys = {"enabled", "sample_rate_in", "sample_rate_out", "codec_in", "codec_out"}
        extra = {k: v for k, v in data.items() if k not in known_keys}

        return cls(
            enabled=data.get("enabled", True),
            sample_rate_in=data.get("sample_rate_in", 16000),
            sample_rate_out=data.get("sample_rate_out", 16000),
            codec_in=data.get("codec_in", "pcm16"),
            codec_out=data.get("codec_out", "pcm16"),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert config to dictionary.

        Returns:
            Configuration as dictionary
        """
        result = {
            "enabled": self.enabled,
            "sample_rate_in": self.sample_rate_in,
            "sample_rate_out": self.sample_rate_out,
            "codec_in": self.codec_in,
            "codec_out": self.codec_out,
        }
        result.update(self.extra)
        return result


def get_transport_config_from_env(transport_name: str) -> dict[str, Any]:
    """
    Load transport configuration from environment variables.

    Environment variable pattern:
        TRANSPORT_{NAME}_{KEY}

    Example:
        TRANSPORT_GENESYS_ENABLED=true
        TRANSPORT_GENESYS_API_KEY=xxx

    Args:
        transport_name: Transport identifier (e.g., "genesys")

    Returns:
        Configuration dictionary
    """
    import os

    prefix = f"TRANSPORT_{transport_name.upper()}_"
    config: dict[str, Any] = {}

    for key, value in os.environ.items():
        if key.startswith(prefix):
            # Convert key to lowercase, strip prefix
            config_key = key[len(prefix) :].lower()

            # Type conversion for known keys
            if config_key == "enabled":
                config[config_key] = value.lower() in ("true", "1", "yes")
            elif config_key in ("sample_rate_in", "sample_rate_out"):
                try:
                    config[config_key] = int(value)
                except ValueError:
                    pass
            else:
                config[config_key] = value

    return config


__all__ = [
    "TransportConfig",
    "get_transport_config_from_env",
]
