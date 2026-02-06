"""
Audio Codec and Sample Rate Utilities
=====================================

Utilities for audio format conversion between transport protocols and
the internal processing pipeline.

Internal formats:
- STT: PCM16LE @ 16kHz (Azure Speech SDK)
- TTS output: PCM16LE @ 16kHz or 24kHz (voice dependent)
- Browser: PCM16LE @ 48kHz

Transport formats:
- ACS: PCM16LE @ 16kHz
- Browser: PCM16LE @ 48kHz
- Genesys: µ-law @ 8kHz (future plugin)
"""

from __future__ import annotations

import struct
from typing import Literal

# µ-law codec constants
ULAW_BIAS = 0x84
ULAW_MAX = 32635
ULAW_CLIP = 32767

# Pre-computed µ-law encode/decode tables for performance
_ULAW_ENCODE_TABLE: list[int] | None = None
_ULAW_DECODE_TABLE: list[int] | None = None


def _init_ulaw_tables() -> None:
    """Initialize µ-law encode/decode lookup tables."""
    global _ULAW_ENCODE_TABLE, _ULAW_DECODE_TABLE

    if _ULAW_ENCODE_TABLE is not None:
        return

    # Decode table: µ-law byte → PCM16 sample
    _ULAW_DECODE_TABLE = []
    for i in range(256):
        ulaw = ~i
        sign = ulaw & 0x80
        exponent = (ulaw >> 4) & 0x07
        mantissa = ulaw & 0x0F
        sample = ((mantissa << 3) + ULAW_BIAS) << exponent
        sample -= ULAW_BIAS
        if sign:
            sample = -sample
        _ULAW_DECODE_TABLE.append(sample)

    # Encode table: PCM16 sample (scaled) → µ-law byte
    # Full table would be 65536 entries; we compute on-demand instead
    _ULAW_ENCODE_TABLE = []


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """
    Convert µ-law audio to PCM16LE.

    Args:
        ulaw_bytes: Raw µ-law (PCMU) audio data

    Returns:
        PCM16 little-endian audio data (2x size of input)
    """
    _init_ulaw_tables()
    assert _ULAW_DECODE_TABLE is not None

    samples = [_ULAW_DECODE_TABLE[b] for b in ulaw_bytes]
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm16_to_ulaw(pcm16_bytes: bytes) -> bytes:
    """
    Convert PCM16LE audio to µ-law.

    Args:
        pcm16_bytes: PCM16 little-endian audio data

    Returns:
        µ-law (PCMU) audio data (half size of input)
    """
    sample_count = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{sample_count}h", pcm16_bytes[: sample_count * 2])

    result = bytearray(sample_count)
    for i, sample in enumerate(samples):
        # Get sign and make positive
        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample

        # Clip to max
        if sample > ULAW_CLIP:
            sample = ULAW_CLIP

        # Add bias
        sample += ULAW_BIAS

        # Find segment (exponent)
        exponent = 7
        exp_mask = 0x4000
        while exponent > 0 and not (sample & exp_mask):
            exponent -= 1
            exp_mask >>= 1

        # Extract mantissa
        mantissa = (sample >> (exponent + 3)) & 0x0F

        # Combine and invert
        result[i] = ~(sign | (exponent << 4) | mantissa) & 0xFF

    return bytes(result)


def resample_linear(
    pcm16_bytes: bytes,
    src_rate: int,
    dst_rate: int,
) -> bytes:
    """
    Resample PCM16 audio using linear interpolation.

    Simple resampler suitable for real-time voice. For higher quality,
    consider using scipy.signal.resample or a proper audio library.

    Args:
        pcm16_bytes: Source PCM16LE audio
        src_rate: Source sample rate (e.g., 8000)
        dst_rate: Destination sample rate (e.g., 16000)

    Returns:
        Resampled PCM16LE audio
    """
    if src_rate == dst_rate:
        return pcm16_bytes

    sample_count = len(pcm16_bytes) // 2
    if sample_count == 0:
        return b""

    samples = struct.unpack(f"<{sample_count}h", pcm16_bytes[: sample_count * 2])

    # Calculate output size
    ratio = dst_rate / src_rate
    output_count = int(sample_count * ratio)

    if output_count == 0:
        return b""

    # Linear interpolation
    output = []
    for i in range(output_count):
        src_pos = i / ratio
        src_idx = int(src_pos)
        frac = src_pos - src_idx

        if src_idx >= sample_count - 1:
            output.append(samples[-1])
        else:
            # Interpolate between adjacent samples
            s0 = samples[src_idx]
            s1 = samples[src_idx + 1]
            interpolated = int(s0 + frac * (s1 - s0))
            output.append(max(-32768, min(32767, interpolated)))

    return struct.pack(f"<{len(output)}h", *output)


def resample_8k_to_16k(pcm16_bytes: bytes) -> bytes:
    """Upsample from 8kHz to 16kHz (2x)."""
    return resample_linear(pcm16_bytes, 8000, 16000)


def resample_16k_to_8k(pcm16_bytes: bytes) -> bytes:
    """Downsample from 16kHz to 8kHz (0.5x)."""
    return resample_linear(pcm16_bytes, 16000, 8000)


def resample_48k_to_16k(pcm16_bytes: bytes) -> bytes:
    """Downsample from 48kHz to 16kHz (1/3x)."""
    return resample_linear(pcm16_bytes, 48000, 16000)


def resample_16k_to_48k(pcm16_bytes: bytes) -> bytes:
    """Upsample from 16kHz to 48kHz (3x)."""
    return resample_linear(pcm16_bytes, 16000, 48000)


def resample_24k_to_16k(pcm16_bytes: bytes) -> bytes:
    """Downsample from 24kHz to 16kHz."""
    return resample_linear(pcm16_bytes, 24000, 16000)


def resample_16k_to_24k(pcm16_bytes: bytes) -> bytes:
    """Upsample from 16kHz to 24kHz."""
    return resample_linear(pcm16_bytes, 16000, 24000)


class AudioConverter:
    """
    Audio format converter for transport adapters.

    Normalizes audio from transport format to internal format (PCM16 @ 16kHz)
    and vice versa.
    """

    def __init__(
        self,
        input_rate: int,
        output_rate: int,
        input_codec: Literal["pcm16", "ulaw", "alaw"] = "pcm16",
        output_codec: Literal["pcm16", "ulaw", "alaw"] = "pcm16",
        internal_rate: int = 16000,
    ):
        """
        Initialize converter.

        Args:
            input_rate: Transport input sample rate
            output_rate: Transport output sample rate
            input_codec: Codec of incoming audio
            output_codec: Codec for outgoing audio
            internal_rate: Internal processing rate (default 16kHz for STT)
        """
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.input_codec = input_codec
        self.output_codec = output_codec
        self.internal_rate = internal_rate

    def to_internal(self, audio_bytes: bytes) -> bytes:
        """
        Convert transport audio to internal format (PCM16 @ 16kHz).

        Args:
            audio_bytes: Audio in transport format

        Returns:
            PCM16LE @ 16kHz for STT processing
        """
        if not audio_bytes:
            return b""

        # Step 1: Decode codec if needed
        if self.input_codec == "ulaw":
            pcm16 = ulaw_to_pcm16(audio_bytes)
        elif self.input_codec == "alaw":
            raise NotImplementedError("A-law codec not yet implemented")
        else:
            pcm16 = audio_bytes

        # Step 2: Resample if needed
        if self.input_rate != self.internal_rate:
            pcm16 = resample_linear(pcm16, self.input_rate, self.internal_rate)

        return pcm16

    def from_internal(self, pcm16_bytes: bytes) -> bytes:
        """
        Convert internal audio to transport format.

        Args:
            pcm16_bytes: PCM16LE @ internal rate (typically 16kHz or 24kHz from TTS)

        Returns:
            Audio in transport format
        """
        if not pcm16_bytes:
            return b""

        # Step 1: Resample if needed
        if self.internal_rate != self.output_rate:
            pcm16 = resample_linear(pcm16_bytes, self.internal_rate, self.output_rate)
        else:
            pcm16 = pcm16_bytes

        # Step 2: Encode codec if needed
        if self.output_codec == "ulaw":
            return pcm16_to_ulaw(pcm16)
        elif self.output_codec == "alaw":
            raise NotImplementedError("A-law codec not yet implemented")
        else:
            return pcm16


__all__ = [
    "ulaw_to_pcm16",
    "pcm16_to_ulaw",
    "resample_linear",
    "resample_8k_to_16k",
    "resample_16k_to_8k",
    "resample_48k_to_16k",
    "resample_16k_to_48k",
    "resample_24k_to_16k",
    "resample_16k_to_24k",
    "AudioConverter",
]
