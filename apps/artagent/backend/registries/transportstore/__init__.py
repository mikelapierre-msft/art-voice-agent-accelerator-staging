"""
Transport Store Registry
========================

Plugin architecture for voice transport providers.

This module enables external transport providers (e.g., Genesys AudioHook, Twilio)
to be developed in separate repositories while sharing the core orchestration,
agent framework, and speech processing capabilities.

Usage:
------
    # Register a builtin transport
    from apps.artagent.backend.registries.transportstore import (
        register_transport,
        get_transport,
        TransportAdapter,
        TransportMessage,
    )

    # Create custom adapter
    class MyTransportAdapter:
        name = "mytransport"
        input_sample_rate = 16000
        ...

    register_transport("mytransport", MyTransportAdapter)

    # Plugin discovery (called at startup)
    from apps.artagent.backend.registries.transportstore import discover_plugins
    discover_plugins()

See Also:
---------
- docs/proposals/transport-plugin-architecture.md
"""

from .protocols import (
    TransportAdapter,
    TransportFactory,
    TransportMessage,
    MessageKind,
)
from .registry import (
    TransportSource,
    TransportDefinition,
    register_transport,
    get_transport,
    get_all_transports,
    list_transports,
    discover_plugins,
    get_plugin_routers,
)
from .config import TransportConfig

__all__ = [
    # Protocols
    "TransportAdapter",
    "TransportFactory",
    "TransportMessage",
    "MessageKind",
    # Registry
    "TransportSource",
    "TransportDefinition",
    "register_transport",
    "get_transport",
    "get_all_transports",
    "list_transports",
    "discover_plugins",
    "get_plugin_routers",
    # Config
    "TransportConfig",
]
