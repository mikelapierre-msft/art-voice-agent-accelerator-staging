"""
Transport Adapter Protocols
===========================

Abstract interfaces for voice transport providers.

Plugins implement these protocols to integrate with the voice system.
The adapter handles wire protocol specifics and normalizes audio to PCM16.

Key Concepts:
-------------
- TransportMessage: Normalized message from any transport
- TransportAdapter: Interface for voice transport providers
- TransportFactory: Factory for creating transport adapters

Audio Normalization:
-------------------
All adapters must convert incoming audio to PCM16 format before passing
to orchestrators. Outgoing audio from TTS is provided as PCM16 and must
be converted to the wire format by the adapter.

Example codec conversions:
- Genesys: µ-law 8kHz ↔ PCM16 16kHz
- ACS: PCM16 16kHz (no conversion needed)
- Browser: PCM16 48kHz (resample only)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import WebSocket


class MessageKind(str, Enum):
    """Normalized message types from any transport."""

    AUDIO = "audio"  # Audio data (PCM16 after conversion)
    METADATA = "metadata"  # Stream metadata (sample rate, channels, etc.)
    DTMF = "dtmf"  # DTMF tone received
    STOP = "stop"  # Request to stop audio playback
    CONTROL = "control"  # Transport-specific control message
    DISCONNECT = "disconnect"  # Session termination


@dataclass
class TransportMessage:
    """
    Normalized message from any transport.

    All transports convert their wire format to this common structure.
    Audio data is always PCM16 after codec conversion.

    Attributes:
        kind: Message type (audio, dtmf, stop, etc.)
        audio_data: PCM16 audio bytes (None for non-audio messages)
        metadata: Transport-specific metadata
        raw: Original message for debugging/logging
        sequence_id: Optional sequence number from transport
    """

    kind: MessageKind
    audio_data: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: bytes | str | None = None
    sequence_id: int | None = None

    @property
    def is_audio(self) -> bool:
        """Check if this is an audio message."""
        return self.kind == MessageKind.AUDIO and self.audio_data is not None

    @property
    def is_silent(self) -> bool:
        """Check if audio is silent (transport-reported)."""
        return self.metadata.get("silent", False)

    def get_dtmf_digit(self) -> str | None:
        """Get DTMF digit if this is a DTMF message."""
        if self.kind == MessageKind.DTMF:
            return self.metadata.get("digit")
        return None


@runtime_checkable
class TransportAdapter(Protocol):
    """
    Interface for voice transport providers.

    Plugins implement this protocol to integrate with the voice system.
    The adapter handles wire protocol specifics and normalizes to PCM16.

    Lifecycle:
        1. Adapter created via factory
        2. start() called to initialize connection
        3. receive_message() called in loop for incoming data
        4. send_audio() / send_stop_audio() for outgoing data
        5. stop() called for cleanup

    Thread Safety:
        Adapters should be safe for concurrent send/receive operations.
        The event loop is managed by the caller.
    """

    # ─── Identity ───
    name: str  # e.g., "genesys", "acs", "browser"

    # ─── Audio Configuration ───
    input_sample_rate: int  # Rate audio arrives at (e.g., 8000 for Genesys)
    output_sample_rate: int  # Rate to send back (may differ from input)
    input_codec: str  # "pcm16", "ulaw", "alaw"
    output_codec: str  # "pcm16", "ulaw", "alaw"

    # ─── Session Identity ───
    session_id: str  # Unique session identifier

    # ─── Lifecycle ───
    async def start(self) -> None:
        """
        Initialize the transport connection.

        Called after adapter creation. May perform handshake,
        authentication, or format negotiation depending on protocol.

        Raises:
            TransportError: If initialization fails
        """
        ...

    async def stop(self) -> None:
        """
        Clean up resources and close connection gracefully.

        Called when session ends. Should handle both normal
        termination and error cleanup.
        """
        ...

    # ─── Message Handling ───
    async def receive_message(self) -> TransportMessage:
        """
        Receive and parse the next message from the transport.

        Returns normalized TransportMessage with PCM16 audio.
        Handles codec conversion internally.

        This method should block until a message is available.
        Internal protocol messages (keepalives, acks) should be
        handled internally and not returned.

        Returns:
            Normalized message with PCM16 audio data

        Raises:
            TransportError: On protocol or connection errors
            asyncio.CancelledError: If receive is cancelled
        """
        ...

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """
        Send audio to the caller.

        Args:
            pcm16_bytes: Audio in PCM16 format at output_sample_rate.
                        Adapter converts to wire format internally.

        Raises:
            TransportError: If send fails
        """
        ...

    async def send_stop_audio(self) -> None:
        """
        Signal to stop/interrupt current audio playback.

        Used for barge-in scenarios where user interrupts agent.
        """
        ...

    # ─── Optional: Transport-Specific Features ───
    async def on_barge_in(self) -> None:
        """
        Handle barge-in event (user interrupts agent).

        Default implementation does nothing. Override for transports
        that need special barge-in handling (e.g., send event to platform).
        """
        ...

    def supports_bidirectional_audio(self) -> bool:
        """
        Check if transport supports sending audio back to caller.

        Returns:
            True if send_audio() is supported, False for monitor-only
        """
        ...


@runtime_checkable
class TransportFactory(Protocol):
    """
    Factory for creating transport adapters.

    Each transport type provides a factory that creates adapter instances
    for new connections.
    """

    @classmethod
    def create(
        cls,
        websocket: WebSocket,
        session_id: str,
        config: dict[str, Any],
    ) -> TransportAdapter:
        """
        Create a new adapter instance for a connection.

        Args:
            websocket: The WebSocket connection
            session_id: Unique session identifier
            config: Transport-specific configuration

        Returns:
            New adapter instance
        """
        ...

    @classmethod
    def get_config_schema(cls) -> dict[str, Any]:
        """
        Return JSON schema for configuration validation.

        Returns:
            JSON Schema dict describing expected config structure
        """
        ...

    @classmethod
    def get_name(cls) -> str:
        """
        Return the transport name.

        Returns:
            Transport identifier (e.g., "acs", "genesys")
        """
        ...


class TransportError(Exception):
    """Base exception for transport errors."""

    def __init__(self, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.recoverable = recoverable


class TransportAuthError(TransportError):
    """Authentication or authorization failed."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, recoverable=False)


class TransportProtocolError(TransportError):
    """Protocol violation or malformed message."""

    def __init__(self, message: str):
        super().__init__(message, recoverable=False)


class TransportConnectionError(TransportError):
    """Connection lost or failed."""

    def __init__(self, message: str = "Connection lost"):
        super().__init__(message, recoverable=True)


__all__ = [
    "MessageKind",
    "TransportMessage",
    "TransportAdapter",
    "TransportFactory",
    "TransportError",
    "TransportAuthError",
    "TransportProtocolError",
    "TransportConnectionError",
]
