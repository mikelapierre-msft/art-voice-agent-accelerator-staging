"""
ACS Transport Adapter
=====================

Transport adapter for Azure Communication Services media streaming.

ACS Wire Protocol:
------------------
- Messages: JSON with `kind` field
- Audio in: Base64-encoded PCM16LE @ 16kHz in "audioData.data"
- Audio out: Base64-encoded PCM16LE @ 16kHz in JSON envelope

Message Types (from ACS):
- AudioMetadata: Stream initialization
- AudioData: Audio samples
- DtmfData: DTMF tones
- StopAudio: Stop playback request
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, ClassVar

from apps.artagent.backend.registries.transportstore.protocols import (
    MessageKind,
    TransportAdapter,
    TransportConnectionError,
    TransportMessage,
    TransportProtocolError,
)
from utils.ml_logging import get_logger

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger("transport.acs")


class ACSMessageKind:
    """ACS WebSocket message types."""

    AUDIO_METADATA = "AudioMetadata"
    AUDIO_DATA = "AudioData"
    DTMF_DATA = "DtmfData"
    STOP_AUDIO = "StopAudio"


class ACSTransportAdapter:
    """
    Transport adapter for Azure Communication Services.

    Handles ACS media streaming protocol with JSON-wrapped audio.
    """

    # Class-level constants for registration
    name: ClassVar[str] = "acs"
    input_sample_rate: ClassVar[int] = 16000
    output_sample_rate: ClassVar[int] = 16000
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
        Initialize ACS transport adapter.

        Args:
            websocket: The WebSocket connection from ACS
            session_id: Unique session identifier
            config: Optional configuration
        """
        self._ws = websocket
        self._session_id = session_id
        self._config = config or {}
        self._metadata_received = False
        self._running = False
        self._sequence_id = 0

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
        logger.info("[%s] ACS transport started", self.session_short)

    async def stop(self) -> None:
        """Clean up resources."""
        self._running = False
        logger.info("[%s] ACS transport stopped", self.session_short)

    async def receive_message(self) -> TransportMessage:
        """
        Receive and parse the next ACS message.

        Returns normalized TransportMessage with PCM16 audio.
        """
        try:
            # ACS sends JSON text messages
            data = await self._ws.receive()

            if data.get("type") == "websocket.disconnect":
                return TransportMessage(
                    kind=MessageKind.DISCONNECT,
                    metadata={"reason": "websocket_closed"},
                )

            # Parse JSON message
            text = data.get("text")
            if not text:
                # Binary messages not expected from ACS
                if "bytes" in data:
                    logger.warning("[%s] Unexpected binary message from ACS", self.session_short)
                    return TransportMessage(kind=MessageKind.CONTROL, metadata={"type": "binary"})
                raise TransportProtocolError("Empty message from ACS")

            message = json.loads(text)
            return self._parse_message(message)

        except json.JSONDecodeError as e:
            raise TransportProtocolError(f"Invalid JSON from ACS: {e}")
        except Exception as e:
            if "disconnect" in str(e).lower():
                return TransportMessage(kind=MessageKind.DISCONNECT, metadata={"error": str(e)})
            raise TransportConnectionError(f"Error receiving ACS message: {e}")

    def _parse_message(self, message: dict[str, Any]) -> TransportMessage:
        """
        Parse an ACS JSON message into normalized format.

        Args:
            message: Parsed JSON message from ACS

        Returns:
            Normalized TransportMessage
        """
        kind = message.get("kind")

        if kind == ACSMessageKind.AUDIO_METADATA:
            self._metadata_received = True
            logger.debug("[%s] ACS metadata received", self.session_short)
            return TransportMessage(
                kind=MessageKind.METADATA,
                metadata={
                    "sample_rate": self.input_sample_rate,
                    "channels": 1,
                    "codec": "pcm16",
                },
                raw=message,
            )

        elif kind == ACSMessageKind.AUDIO_DATA:
            audio_section = message.get("audioData", {}) or {}
            audio_b64 = audio_section.get("data")

            if not audio_b64:
                return TransportMessage(
                    kind=MessageKind.AUDIO,
                    audio_data=b"",
                    metadata={"silent": True},
                    raw=message,
                )

            # Decode base64 audio
            audio_bytes = base64.b64decode(audio_b64)
            is_silent = audio_section.get("silent", False)
            sequence_id = audio_section.get("sequenceId")

            return TransportMessage(
                kind=MessageKind.AUDIO,
                audio_data=audio_bytes,
                metadata={"silent": is_silent},
                sequence_id=sequence_id,
                raw=message,
            )

        elif kind == ACSMessageKind.DTMF_DATA:
            dtmf_section = message.get("dtmfData", {})
            tone = dtmf_section.get("tone")
            return TransportMessage(
                kind=MessageKind.DTMF,
                metadata={"digit": tone},
                raw=message,
            )

        elif kind == ACSMessageKind.STOP_AUDIO:
            logger.info("[%s] ACS StopAudio received", self.session_short)
            return TransportMessage(
                kind=MessageKind.STOP,
                raw=message,
            )

        else:
            logger.debug("[%s] Unknown ACS message kind: %s", self.session_short, kind)
            return TransportMessage(
                kind=MessageKind.CONTROL,
                metadata={"acs_kind": kind},
                raw=message,
            )

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """
        Send audio to ACS (for TTS playback).

        Args:
            pcm16_bytes: PCM16 audio at 16kHz
        """
        if not pcm16_bytes:
            return

        # Encode as base64
        b64_audio = base64.b64encode(pcm16_bytes).decode("utf-8")

        # Create ACS message envelope
        message = {
            "kind": "AudioData",
            "audioData": {
                "data": b64_audio,
                "timestamp": None,
                "participantRawID": None,
                "silent": False,
            },
        }

        try:
            await self._ws.send_json(message)
            self._sequence_id += 1
        except Exception as e:
            raise TransportConnectionError(f"Failed to send audio to ACS: {e}")

    async def send_stop_audio(self) -> None:
        """
        Send stop audio signal to ACS.
        
        Note: ACS doesn't have a direct stop mechanism via WebSocket.
        The client-side handles interruption.
        """
        logger.debug("[%s] Stop audio requested (ACS handles via client)", self.session_short)

    async def on_barge_in(self) -> None:
        """Handle barge-in event."""
        logger.debug("[%s] Barge-in signaled", self.session_short)

    def supports_bidirectional_audio(self) -> bool:
        """ACS supports bidirectional audio."""
        return True

    @classmethod
    def create(
        cls,
        websocket: WebSocket,
        session_id: str,
        config: dict[str, Any] | None = None,
    ) -> "ACSTransportAdapter":
        """Factory method to create adapter instance."""
        return cls(websocket=websocket, session_id=session_id, config=config)

    @classmethod
    def get_config_schema(cls) -> dict[str, Any]:
        """Return JSON schema for configuration."""
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

    @classmethod
    def get_name(cls) -> str:
        """Return transport name."""
        return cls.name


__all__ = ["ACSTransportAdapter", "ACSMessageKind"]
