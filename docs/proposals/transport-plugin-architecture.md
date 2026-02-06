# Transport Plugin Architecture

> **Status:** Implemented (Phase 1 + Phase 2)  
> **Created:** 2026-02-05  
> **Updated:** 2026-02-05  
> **Purpose:** Enable external transport providers (e.g., Genesys AudioHook) as plugins

---

## Implementation Status

**Phase 1 Complete:** Core plugin infrastructure is in place.

| Component | Status | Location |
|-----------|--------|----------|
| TransportAdapter protocol | ✅ Done | `registries/transportstore/protocols.py` |
| Transport registry | ✅ Done | `registries/transportstore/registry.py` |
| Transport config | ✅ Done | `registries/transportstore/config.py` |
| ACS adapter | ✅ Done | `voice/transports/acs.py` |
| Browser adapter | ✅ Done | `voice/transports/browser.py` |
| Plugin discovery | ✅ Done | `lifecycle/steps.py` + `main.py` |
| Entry points | ✅ Done | `pyproject.toml` |
| VOICELIVE cleanup | ✅ Done | Removed from `TransportType` enum |

**Phase 2 Complete:** Handler integration and codec utilities.

| Component | Status | Location |
|-----------|--------|----------|
| VoiceHandler adapter support | ✅ Done | `voice/handler.py` (new `run_with_adapter()`) |
| media.py uses adapter | ✅ Done | `api/v1/endpoints/media.py` |
| browser.py uses adapter | ✅ Done | `api/v1/endpoints/browser.py` |
| Audio codec utilities | ✅ Done | `voice/transports/codecs.py` |
| µ-law ↔ PCM16 conversion | ✅ Done | `ulaw_to_pcm16()`, `pcm16_to_ulaw()` |
| Sample rate conversion | ✅ Done | `resample_linear()`, `AudioConverter` |

**Next Steps (Phase 3 - Optional):**
- Genesys AudioHook plugin (separate repo)

---

## Executive Summary

This proposal defines a plugin architecture for voice transport providers, allowing third-party integrations (like Genesys AudioHook) to be developed in separate repositories while sharing the core orchestration, agent framework, and speech processing capabilities.

**Key insight:** The codebase has two orthogonal dimensions:
1. **Transport** (wire protocol): How audio arrives — Browser, ACS, Genesys AudioHook
2. **Orchestration** (processing): How voice is processed — SpeechCascade or VoiceLive

Plugins address the **transport layer only**. Orchestrators remain internal.

---

## Current Architecture

### Transport Types (Actual Usage)

| Transport | Wire Protocol | Sample Rate | Orchestration |
|-----------|---------------|-------------|---------------|
| **Browser** | Raw PCM16 WebSocket | 48kHz | SpeechCascade or VoiceLive |
| **ACS** | JSON-wrapped PCM16 | 16kHz | SpeechCascade or VoiceLive |
| ~~VOICELIVE~~ | _Unused enum value_ | — | — |

**Note:** `TransportType.VOICELIVE` is dead code. VoiceLive is an orchestration mode, not a transport. The `VoiceLiveSDKHandler` uses its own `VoiceLiveTransport = Literal["acs", "realtime"]` internally.

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    External Systems                              │
│        Phone ──► ACS    Browser ──► WebSocket                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Transport Layer                               │
│     media.py (ACS)        browser.py (Browser)                  │
│     JSON + Base64 PCM     Raw binary PCM                        │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Orchestration Layer                           │
│     SpeechCascade: STT ──► LLM ──► TTS (component control)      │
│     VoiceLive: OpenAI Realtime API (native voice-to-voice)      │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│              Shared Services (agents, tools, memory)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Proposed Architecture

### Plugin Boundary

```
┌─────────────────────────────────────────────────────────────────┐
│  PLUGIN TERRITORY (external repos)                              │
│                                                                  │
│    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│    │   Genesys   │  │   Twilio    │  │   Custom    │           │
│    │  AudioHook  │  │   Streams   │  │  Transport  │           │
│    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │
│           │                │                │                    │
└───────────┼────────────────┼────────────────┼────────────────────┘
            │                │                │
            ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│  CORE TERRITORY (this repo)                                     │
│                                                                  │
│    ┌──────────────────────────────────────────────┐             │
│    │         TransportAdapter Protocol            │◄── Interface │
│    └──────────────────────────────────────────────┘             │
│                          │                                       │
│    ┌─────────────────────┼─────────────────────┐                │
│    │                     ▼                     │                 │
│    │  ┌─────────────┐  ┌─────────────┐        │                 │
│    │  │   Browser   │  │     ACS     │        │ Builtin        │
│    │  │   Adapter   │  │   Adapter   │        │ Adapters       │
│    │  └─────────────┘  └─────────────┘        │                 │
│    └───────────────────────────────────────────┘                │
│                          │                                       │
│    ┌─────────────────────▼─────────────────────┐                │
│    │     Orchestrators, Agents, Tools, Memory   │                │
│    └────────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

### Transport Adapter Protocol

```python
# apps/artagent/backend/registries/transportstore/protocols.py

from typing import Protocol, Any
from dataclasses import dataclass

@dataclass
class TransportMessage:
    """Normalized message from any transport."""
    kind: str                    # "audio", "metadata", "dtmf", "stop", "control"
    audio_data: bytes | None     # PCM16 audio (after codec conversion)
    metadata: dict[str, Any]     # Transport-specific metadata
    raw: bytes | str             # Original message for debugging


class TransportAdapter(Protocol):
    """
    Interface for voice transport providers.
    
    Plugins implement this protocol to integrate with the voice system.
    The adapter handles wire protocol specifics and normalizes to PCM16.
    """
    
    # ─── Identity ───
    name: str                    # e.g., "genesys", "twilio"
    
    # ─── Audio Configuration ───
    input_sample_rate: int       # Rate audio arrives at (e.g., 8000 for Genesys)
    output_sample_rate: int      # Rate to send back (may differ from input)
    input_codec: str             # "pcm16", "ulaw", "alaw"
    output_codec: str            # "pcm16", "ulaw", "alaw"
    
    # ─── Lifecycle ───
    async def start(self) -> None:
        """Initialize the transport connection."""
        ...
    
    async def stop(self) -> None:
        """Clean up resources."""
        ...
    
    # ─── Message Handling ───
    async def receive_message(self) -> TransportMessage:
        """
        Receive and parse the next message from the transport.
        
        Returns normalized TransportMessage with PCM16 audio.
        Handles codec conversion internally.
        """
        ...
    
    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """
        Send audio to the caller.
        
        Args:
            pcm16_bytes: Audio in PCM16 format at output_sample_rate.
                        Adapter converts to wire format internally.
        """
        ...
    
    async def send_stop_audio(self) -> None:
        """Signal to stop/interrupt current audio playback."""
        ...
    
    # ─── Optional: Transport-Specific Features ───
    async def on_barge_in(self) -> None:
        """Handle barge-in event (user interrupts agent)."""
        ...


class TransportFactory(Protocol):
    """Factory for creating transport adapters."""
    
    @classmethod
    def create(
        cls,
        websocket: Any,
        config: dict[str, Any],
    ) -> TransportAdapter:
        """Create a new adapter instance for a connection."""
        ...
    
    @classmethod
    def get_config_schema(cls) -> dict[str, Any]:
        """Return JSON schema for configuration validation."""
        ...
```

### Transport Registry

```python
# apps/artagent/backend/registries/transportstore/registry.py

from enum import Enum
from dataclasses import dataclass
from typing import Any
from importlib.metadata import entry_points

class TransportSource(str, Enum):
    """Origin of a transport adapter."""
    BUILTIN = "builtin"
    PLUGIN = "plugin"

@dataclass
class TransportDefinition:
    """Registered transport adapter."""
    name: str
    factory: type
    config_schema: dict[str, Any]
    source: TransportSource
    input_sample_rate: int
    output_sample_rate: int
    input_codec: str
    output_codec: str

_TRANSPORTS: dict[str, TransportDefinition] = {}

def register_transport(
    name: str,
    factory: type,
    *,
    input_sample_rate: int = 16000,
    output_sample_rate: int = 16000,
    input_codec: str = "pcm16",
    output_codec: str = "pcm16",
    source: TransportSource = TransportSource.BUILTIN,
) -> None:
    """Register a transport adapter."""
    _TRANSPORTS[name] = TransportDefinition(
        name=name,
        factory=factory,
        config_schema=factory.get_config_schema(),
        source=source,
        input_sample_rate=input_sample_rate,
        output_sample_rate=output_sample_rate,
        input_codec=input_codec,
        output_codec=output_codec,
    )

def get_transport(name: str) -> TransportDefinition | None:
    """Get a registered transport by name."""
    return _TRANSPORTS.get(name)

def list_transports() -> list[TransportDefinition]:
    """List all registered transports."""
    return list(_TRANSPORTS.values())

def discover_plugins() -> None:
    """
    Load transport plugins from entry_points.
    
    Plugins register via pyproject.toml:
        [project.entry-points."artagent.transports"]
        genesys = "genesys_transport:GenesysTransportAdapter"
    """
    eps = entry_points(group="artagent.transports")
    for ep in eps:
        try:
            adapter_cls = ep.load()
            register_transport(
                name=ep.name,
                factory=adapter_cls,
                input_sample_rate=getattr(adapter_cls, "input_sample_rate", 16000),
                output_sample_rate=getattr(adapter_cls, "output_sample_rate", 16000),
                input_codec=getattr(adapter_cls, "input_codec", "pcm16"),
                output_codec=getattr(adapter_cls, "output_codec", "pcm16"),
                source=TransportSource.PLUGIN,
            )
        except Exception as e:
            logger.error(f"Failed to load transport plugin {ep.name}: {e}")
```

---

## Genesys AudioHook Plugin

### Protocol Comparison

| Aspect | ACS | Genesys AudioHook |
|--------|-----|-------------------|
| **Connection** | ACS initiates WebSocket to our endpoint | Genesys initiates WebSocket |
| **Audio codec** | PCM16LE | PCMU (µ-law) |
| **Sample rate** | 16kHz | 8kHz |
| **Audio framing** | Base64 in JSON | Raw binary WebSocket frames |
| **Message format** | `{"kind": "AudioData", ...}` | `{"version": "2", "type": "open", ...}` |
| **Sequence tracking** | Simple `sequenceId` | Complex `seq`/`serverseq`/`clientseq` |
| **Session lifecycle** | REST callbacks (Event Grid) | In-band open/close transactions |
| **Auth** | ACS JWT validation | X-API-KEY + HMAC signature |
| **Keepalive** | WebSocket ping/pong | Protocol-level `ping`/`pong` messages |
| **Barge-in** | Implicit via audio | Explicit `BargeIn` event entity |

### AudioHook Message Types

**Client → Server (from Genesys):**
- `open` — Session initialization with media format negotiation
- `close` — Graceful session termination
- `ping` — Keepalive (requires immediate `pong` response)
- `dtmf` — DTMF digit received
- `update` — Language or config change
- `pause` / `resume` — Audio stream control
- `discarded` — Audio was lost/dropped
- Binary frames — Raw µ-law audio

**Server → Client (to Genesys):**
- `opened` — Accept session, select media format
- `closed` — Acknowledge session end
- `pong` — Keepalive response
- `disconnect` — Server-initiated termination
- `event` — Metadata (transcripts, barge-in, bot responses)
- `pause` / `resume` — Request audio stream control
- Binary frames — TTS audio (Audio Connector feature)

### Plugin Structure (Separate Repo)

```
artagent-transport-genesys/
├── pyproject.toml
├── README.md
├── src/
│   └── genesys_transport/
│       ├── __init__.py          # Exports GenesysTransportAdapter
│       ├── adapter.py           # TransportAdapter implementation
│       ├── protocol.py          # AudioHook message types & state machine
│       ├── codecs.py            # µ-law ↔ PCM16 conversion
│       ├── auth.py              # X-API-KEY + HMAC signature validation
│       └── session.py           # Sequence number tracking
└── tests/
    ├── test_adapter.py
    ├── test_codecs.py
    └── test_protocol.py
```

### Plugin pyproject.toml

```toml
[project]
name = "artagent-transport-genesys"
version = "1.0.0"
description = "Genesys AudioHook transport adapter for ART Voice Agent"
requires-python = ">=3.11"
dependencies = [
    "artagent-backend>=1.0",  # Core protocols
]

[project.entry-points."artagent.transports"]
genesys = "genesys_transport:GenesysTransportAdapter"
```

### GenesysTransportAdapter Implementation

```python
# src/genesys_transport/adapter.py

from artagent.backend.registries.transportstore.protocols import (
    TransportAdapter,
    TransportMessage,
)
from .protocol import AudioHookProtocol, MessageType
from .codecs import ulaw_to_pcm16, pcm16_to_ulaw, resample
from .auth import validate_signature

class GenesysTransportAdapter:
    """Genesys AudioHook transport adapter."""
    
    name = "genesys"
    input_sample_rate = 8000
    output_sample_rate = 8000
    input_codec = "ulaw"
    output_codec = "ulaw"
    
    def __init__(self, websocket, config: dict):
        self._ws = websocket
        self._config = config
        self._protocol = AudioHookProtocol()
        self._session_id: str | None = None
        
    async def start(self) -> None:
        """Handle AudioHook open transaction."""
        # Wait for 'open' message from Genesys
        open_msg = await self._receive_raw()
        self._protocol.handle_open(open_msg)
        
        # Send 'opened' response with selected media format
        await self._send_opened(media_format="PCMU", channels=["external"], rate=8000)
        
    async def receive_message(self) -> TransportMessage:
        """Receive and normalize AudioHook messages."""
        while True:
            msg = await self._receive_raw()
            
            if msg.is_binary:
                # Binary = audio frame (µ-law)
                pcm16 = ulaw_to_pcm16(msg.data)
                # Resample 8kHz → 16kHz for STT
                pcm16_16k = resample(pcm16, 8000, 16000)
                return TransportMessage(
                    kind="audio",
                    audio_data=pcm16_16k,
                    metadata={"position": self._protocol.position},
                    raw=msg.data,
                )
            
            # Text = JSON control message
            parsed = self._protocol.parse(msg.text)
            
            if parsed.type == MessageType.PING:
                await self._send_pong(parsed.seq)
                continue
                
            if parsed.type == MessageType.CLOSE:
                await self._send_closed()
                return TransportMessage(kind="disconnect", audio_data=None, metadata={}, raw=msg.text)
                
            if parsed.type == MessageType.DTMF:
                return TransportMessage(
                    kind="dtmf",
                    audio_data=None,
                    metadata={"digit": parsed.parameters.digit},
                    raw=msg.text,
                )
                
            if parsed.type == MessageType.PAUSE:
                await self._send_paused()
                continue  # Don't surface to orchestrator
                
    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """Send audio back to Genesys (Audio Connector feature)."""
        # Resample 16kHz → 8kHz
        pcm16_8k = resample(pcm16_bytes, 16000, 8000)
        # Convert to µ-law
        ulaw = pcm16_to_ulaw(pcm16_8k)
        # Send as binary WebSocket frame
        await self._ws.send_bytes(ulaw)
        
    async def send_stop_audio(self) -> None:
        """Send barge-in event to stop playback."""
        await self._send_event(entity_type="barge_in", data={})
        
    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
                "client_secret": {"type": "string"},
                "validate_signature": {"type": "boolean", "default": True},
            },
            "required": ["api_key"],
        }
```

---

## Implementation Plan

### Phase 1: Define Protocol Interfaces

**Files to create:**
- `apps/artagent/backend/registries/transportstore/__init__.py`
- `apps/artagent/backend/registries/transportstore/protocols.py`
- `apps/artagent/backend/registries/transportstore/registry.py`
- `apps/artagent/backend/registries/transportstore/config.py`

**Changes:**
- No modifications to existing handlers yet
- Protocols are abstract — existing code unaffected

### Phase 2: Extract Builtin Adapters

**Files to create:**
- `apps/artagent/backend/voice/transports/__init__.py`
- `apps/artagent/backend/voice/transports/browser.py`
- `apps/artagent/backend/voice/transports/acs.py`

**Files to modify:**
- `apps/artagent/backend/voice/handler.py` — Extract ACS message parsing (~150 lines)
- `apps/artagent/backend/api/v1/endpoints/media.py` — Use adapter from registry
- `apps/artagent/backend/api/v1/endpoints/browser.py` — Use adapter from registry
- `apps/artagent/backend/voice/shared/context.py` — Remove `VOICELIVE` from `TransportType`

### Phase 3: Plugin Discovery

**Files to modify:**
- `pyproject.toml` — Add entry_points section for builtin adapters
- `apps/artagent/backend/main.py` — Call `discover_plugins()` at startup
- `apps/artagent/backend/config/settings.py` — Add `TRANSPORT_*` config pattern

### Phase 4: Genesys Plugin (Separate Repo)

**New repository:** `artagent-transport-genesys`

**Deliverables:**
- Complete AudioHook protocol implementation
- µ-law ↔ PCM16 codec conversion
- HMAC signature authentication
- Unit tests with mock Genesys client
- Documentation

---

## Configuration

### Environment Variables

```bash
# Builtin transports (always available)
# No configuration needed — they just work

# Plugin transports (loaded via entry_points)
TRANSPORT_GENESYS_ENABLED=true
TRANSPORT_GENESYS_API_KEY=your-api-key
TRANSPORT_GENESYS_CLIENT_SECRET=your-secret
TRANSPORT_GENESYS_VALIDATE_SIGNATURE=true
```

### Runtime Behavior

1. On startup: `discover_plugins()` loads all entry_points
2. Each enabled transport registers with the registry
3. Endpoint detects transport type from connection (path, headers, or protocol sniffing)
4. Adapter instance created for each connection
5. All adapters speak PCM16 to orchestrators

---

## Verification

### Unit Tests
- Protocol compliance for each adapter
- Codec conversion accuracy (µ-law ↔ PCM16 round-trip)
- Sequence number tracking (Genesys)
- Message parsing edge cases

### Integration Tests
- Plugin discovery with mock entry_points
- Multi-transport concurrent sessions
- Adapter hot-swap scenarios

### Manual Tests
- Real Genesys Cloud integration test
- Latency measurements vs ACS baseline
- Barge-in responsiveness

---

## Decisions

| Decision | Rationale |
|----------|-----------|
| **Entry points over manual config** | Standard Python mechanism, works with pip/Docker |
| **Protocol (ABC) over inheritance** | Plugins don't inherit implementation, just interface |
| **Separate endpoint per transport** | `/media/stream` (ACS), `/audiohook/stream` (Genesys) — cleaner routing |
| **Codec conversion in adapter** | Orchestrators always work with PCM16 |
| **`VOICELIVE` enum removal** | Dead code — VoiceLive is orchestration, not transport |
| **SpeechCascade only for Genesys v1** | VoiceLive compatibility (8kHz→24kHz) deferred |

---

## Open Questions

1. **Endpoint routing**: Single `/stream` with protocol detection, or separate paths per transport?
2. **Genesys + VoiceLive**: Is 8kHz µ-law → 24kHz PCM16 quality acceptable for OpenAI Realtime?
3. **Plugin versioning**: How to handle protocol version drift between core and plugins?
4. **Telemetry**: Should adapters emit their own spans, or is core instrumentation sufficient?

---

## References

- [Genesys AudioHook Protocol Specification](https://developer.genesys.cloud/devapps/audiohook/)
- [ACS Media Streaming](https://learn.microsoft.com/en-us/azure/communication-services/concepts/voice-video-calling/media-streaming)
- [Python Entry Points](https://packaging.python.org/en/latest/specifications/entry-points/)
- [Voice Handler Architecture](../architecture/backend-voice-agents-architecture.md)
