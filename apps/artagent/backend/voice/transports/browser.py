"""
Browser Transport Adapter
=========================

Transport adapter for browser-based WebSocket connections.

Browser Wire Protocol:
----------------------
- Audio in: Raw PCM16LE bytes @ 48kHz
- Audio out: Raw PCM16LE bytes @ 48kHz
- Control: JSON text messages

The browser sends:
- Binary frames: Raw PCM16 audio
- Text frames: JSON control messages (commands, config)

The server sends:
- Binary frames: TTS audio (PCM16)
- Text frames: JSON events, transcripts, status
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from apps.artagent.backend.registries.transportstore.protocols import (
    MessageKind,
    TransportAdapter,
    TransportConnectionError,
    TransportMessage,
)
from utils.ml_logging import get_logger

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger("transport.browser")


class BrowserTransportAdapter:
    """
    Transport adapter for browser WebSocket connections.

    Handles raw PCM16 audio without JSON wrapping.
    """

    # Class-level constants for registration
    name: ClassVar[str] = "browser"
    input_sample_rate: ClassVar[int] = 48000
    output_sample_rate: ClassVar[int] = 48000
    input_codec: ClassVar[str] = "pcm16"
    output_codec: ClassVar[str] = "pcm16"
    supports_bidirectional: ClassVar[bool] = True

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize browser transport adapter.

        Args:
            websocket: The WebSocket connection from browser
            session_id: Unique session identifier
            config: Optional configuration
        """
        self._ws = websocket
        self._session_id = session_id
        self._config = config or {}
        self._running = False

    @property
    def session_id(self) -> str:
        """Get session identifier."""
        return self._session_id

    @property
    def session_short(self) -> str:
        """Get short session ID for logging."""
        return self._session_id[-8:] if self._session_id else "unknown"

    async def start(self) -> None:
        """Initialize the transport connection."""
        self._running = True
        logger.info("[%s] Browser transport started", self.session_short)

    async def stop(self) -> None:
        """Clean up resources."""
        self._running = False
        logger.info("[%s] Browser transport stopped", self.session_short)

    async def receive_message(self) -> TransportMessage:
        """
        Receive and parse the next browser message.

        Returns normalized TransportMessage with PCM16 audio.
        """
        try:
            data = await self._ws.receive()
            msg_type = data.get("type")

            if msg_type == "websocket.disconnect":
                return TransportMessage(
                    kind=MessageKind.DISCONNECT,
                    metadata={"reason": "websocket_closed"},
                )

            if msg_type == "websocket.receive":
                # Binary = raw audio
                if "bytes" in data:
                    return self._parse_audio(data["bytes"])

                # Text = JSON control message
                if "text" in data:
                    return self._parse_control(data["text"])

            # Unknown message type
            return TransportMessage(
                kind=MessageKind.CONTROL,
                metadata={"ws_type": msg_type},
            )

        except Exception as e:
            if "disconnect" in str(e).lower():
                return TransportMessage(kind=MessageKind.DISCONNECT, metadata={"error": str(e)})
            raise TransportConnectionError(f"Error receiving browser message: {e}")

    def _parse_audio(self, audio_bytes: bytes) -> TransportMessage:
        """
        Parse raw audio bytes from browser.

        Args:
            audio_bytes: Raw PCM16 audio

        Returns:
            Normalized audio message
        """
        return TransportMessage(
            kind=MessageKind.AUDIO,
            audio_data=audio_bytes,
            metadata={"silent": False},
            raw=audio_bytes,
        )

    def _parse_control(self, text: str) -> TransportMessage:
        """
        Parse JSON control message from browser.

        Args:
            text: JSON text message

        Returns:
            Normalized control message
        """
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("[%s] Invalid JSON from browser: %s", self.session_short, text[:100])
            return TransportMessage(
                kind=MessageKind.CONTROL,
                metadata={"raw_text": text},
            )

        msg_type = message.get("type", "")

        # Handle known control message types
        if msg_type == "stop":
            return TransportMessage(
                kind=MessageKind.STOP,
                metadata=message,
                raw=text,
            )

        if msg_type == "dtmf":
            digit = message.get("digit")
            return TransportMessage(
                kind=MessageKind.DTMF,
                metadata={"digit": digit},
                raw=text,
            )

        if msg_type == "config":
            return TransportMessage(
                kind=MessageKind.METADATA,
                metadata=message,
                raw=text,
            )

        # Generic control message
        return TransportMessage(
            kind=MessageKind.CONTROL,
            metadata=message,
            raw=text,
        )

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """
        Send audio to browser (for TTS playback).

        Args:
            pcm16_bytes: PCM16 audio at 48kHz
        """
        if not pcm16_bytes:
            return

        try:
            # Browser receives raw bytes
            await self._ws.send_bytes(pcm16_bytes)
        except Exception as e:
            raise TransportConnectionError(f"Failed to send audio to browser: {e}")

    async def send_stop_audio(self) -> None:
        """
        Send stop audio signal to browser.

        Browser will clear its audio queue.
        """
        try:
            message = {"type": "audio_stop"}
            await self._ws.send_json(message)
            logger.debug("[%s] Sent audio_stop to browser", self.session_short)
        except Exception as e:
            logger.warning("[%s] Failed to send audio_stop: %s", self.session_short, e)

    async def on_barge_in(self) -> None:
        """Handle barge-in event."""
        await self.send_stop_audio()

    def supports_bidirectional_audio(self) -> bool:
        """Browser supports bidirectional audio."""
        return True

    @classmethod
    def create(
        cls,
        websocket: WebSocket,
        session_id: str,
        config: dict[str, Any] | None = None,
    ) -> "BrowserTransportAdapter":
        """Factory method to create adapter instance."""
        return cls(websocket=websocket, session_id=session_id, config=config)

    @classmethod
    def get_config_schema(cls) -> dict[str, Any]:
        """Return JSON schema for configuration."""
        return {
            "type": "object",
            "properties": {
                "sample_rate": {
                    "type": "integer",
                    "default": 48000,
                    "description": "Audio sample rate in Hz",
                },
            },
            "additionalProperties": True,
        }

    @classmethod
    def get_name(cls) -> str:
        """Return transport name."""
        return cls.name


__all__ = ["BrowserTransportAdapter"]
