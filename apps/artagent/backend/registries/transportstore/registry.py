"""
Transport Registry
==================

Central registry for transport adapters with plugin discovery support.

Transports can be registered in two ways:
1. Builtin: Registered directly in code (ACS, Browser)
2. Plugin: Discovered via entry_points at startup

Plugin Discovery:
-----------------
External packages register transports via pyproject.toml:

    [project.entry-points."artagent.transports"]
    genesys = "genesys_transport:GenesysTransportAdapter"

At startup, discover_plugins() loads these automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from utils.ml_logging import get_logger

if TYPE_CHECKING:
    from .protocols import TransportAdapter

logger = get_logger("transportstore.registry")


class TransportSource(str, Enum):
    """Origin of a transport adapter."""

    BUILTIN = "builtin"  # Shipped with core (ACS, Browser)
    PLUGIN = "plugin"  # Loaded from entry_points


@dataclass
class TransportDefinition:
    """
    Registered transport adapter definition.

    Contains the factory class and metadata for a transport type.
    """

    name: str
    factory: type
    config_schema: dict[str, Any]
    source: TransportSource
    input_sample_rate: int
    output_sample_rate: int
    input_codec: str
    output_codec: str
    supports_bidirectional: bool = True

    def create_adapter(
        self,
        websocket: Any,
        session_id: str,
        config: dict[str, Any] | None = None,
    ) -> TransportAdapter:
        """
        Create a new adapter instance.

        Args:
            websocket: The WebSocket connection
            session_id: Unique session identifier
            config: Transport-specific configuration

        Returns:
            New adapter instance
        """
        return self.factory.create(
            websocket=websocket,
            session_id=session_id,
            config=config or {},
        )


# Global registry
_TRANSPORTS: dict[str, TransportDefinition] = {}
_PLUGIN_ROUTERS: list[Any] = []  # FastAPI routers provided by transport plugins


def register_transport(
    name: str,
    factory: type,
    *,
    input_sample_rate: int = 16000,
    output_sample_rate: int = 16000,
    input_codec: str = "pcm16",
    output_codec: str = "pcm16",
    supports_bidirectional: bool = True,
    source: TransportSource = TransportSource.BUILTIN,
) -> None:
    """
    Register a transport adapter.

    Args:
        name: Unique transport identifier (e.g., "acs", "genesys")
        factory: Class implementing TransportFactory protocol
        input_sample_rate: Sample rate of incoming audio
        output_sample_rate: Sample rate for outgoing audio
        input_codec: Codec of incoming audio ("pcm16", "ulaw", "alaw")
        output_codec: Codec for outgoing audio
        supports_bidirectional: Whether transport can send audio back
        source: Whether builtin or plugin

    Raises:
        ValueError: If transport with same name already registered
    """
    if name in _TRANSPORTS:
        existing = _TRANSPORTS[name]
        # Allow re-registration from same source (e.g., during testing)
        if existing.source != source:
            raise ValueError(
                f"Transport '{name}' already registered from {existing.source.value}"
            )
        logger.debug("Re-registering transport '%s'", name)

    # Get config schema from factory
    config_schema = {}
    if hasattr(factory, "get_config_schema"):
        try:
            config_schema = factory.get_config_schema()
        except Exception as e:
            logger.warning("Failed to get config schema for %s: %s", name, e)

    definition = TransportDefinition(
        name=name,
        factory=factory,
        config_schema=config_schema,
        source=source,
        input_sample_rate=input_sample_rate,
        output_sample_rate=output_sample_rate,
        input_codec=input_codec,
        output_codec=output_codec,
        supports_bidirectional=supports_bidirectional,
    )

    _TRANSPORTS[name] = definition
    logger.info(
        "Registered transport '%s' (source=%s, codec=%s@%dHz)",
        name,
        source.value,
        input_codec,
        input_sample_rate,
    )


def unregister_transport(name: str) -> bool:
    """
    Unregister a transport adapter.

    Args:
        name: Transport identifier

    Returns:
        True if transport was removed, False if not found
    """
    if name in _TRANSPORTS:
        del _TRANSPORTS[name]
        logger.info("Unregistered transport '%s'", name)
        return True
    return False


def get_transport(name: str) -> TransportDefinition | None:
    """
    Get a registered transport by name.

    Args:
        name: Transport identifier

    Returns:
        Transport definition or None if not found
    """
    return _TRANSPORTS.get(name)


def get_all_transports() -> dict[str, TransportDefinition]:
    """
    Get all registered transports.

    Returns:
        Dictionary of transport name to definition
    """
    return _TRANSPORTS.copy()


def list_transports() -> list[TransportDefinition]:
    """
    List all registered transports.

    Returns:
        List of all transport definitions
    """
    return list(_TRANSPORTS.values())


def list_transport_names() -> list[str]:
    """
    List names of all registered transports.

    Returns:
        List of transport names
    """
    return list(_TRANSPORTS.keys())


def get_plugin_routers() -> list[Any]:
    """
    Get FastAPI routers provided by transport plugins.

    Plugins can supply a ``get_router()`` class method on their factory.
    Routers are collected during :func:`discover_plugins` and should be
    mounted on the FastAPI app at startup.

    Returns:
        List of FastAPI APIRouter instances from plugins.
    """
    return _PLUGIN_ROUTERS.copy()


def clear_transports() -> None:
    """
    Clear all registered transports.

    Primarily for testing.
    """
    _TRANSPORTS.clear()
    _PLUGIN_ROUTERS.clear()
    logger.debug("Cleared all transports")


def discover_plugins() -> int:
    """
    Load transport plugins from entry_points.

    Plugins register via pyproject.toml:
        [project.entry-points."artagent.transports"]
        genesys = "genesys_transport:GenesysTransportAdapter"

    Returns:
        Number of plugins discovered

    Note:
        Errors loading individual plugins are logged but don't
        prevent other plugins from loading.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:
        from importlib_metadata import entry_points  # type: ignore

    discovered = 0

    try:
        # Python 3.10+ style
        eps = entry_points(group="artagent.transports")
    except TypeError:
        # Python 3.9 style
        all_eps = entry_points()
        eps = all_eps.get("artagent.transports", [])

    for ep in eps:
        try:
            logger.debug("Loading transport plugin: %s", ep.name)
            adapter_cls = ep.load()

            # Extract metadata from adapter class
            input_rate = getattr(adapter_cls, "input_sample_rate", 16000)
            output_rate = getattr(adapter_cls, "output_sample_rate", 16000)
            input_codec = getattr(adapter_cls, "input_codec", "pcm16")
            output_codec = getattr(adapter_cls, "output_codec", "pcm16")
            bidirectional = getattr(adapter_cls, "supports_bidirectional", True)

            register_transport(
                name=ep.name,
                factory=adapter_cls,
                input_sample_rate=input_rate,
                output_sample_rate=output_rate,
                input_codec=input_codec,
                output_codec=output_codec,
                supports_bidirectional=bidirectional,
                source=TransportSource.PLUGIN,
            )

            # Collect router if the plugin provides one
            if hasattr(adapter_cls, "get_router"):
                try:
                    plugin_router = adapter_cls.get_router()
                    _PLUGIN_ROUTERS.append(plugin_router)
                    logger.info("Loaded router from transport plugin '%s'", ep.name)
                except Exception as router_err:
                    logger.warning(
                        "Failed to load router from plugin '%s': %s",
                        ep.name,
                        router_err,
                    )

            discovered += 1

        except Exception as e:
            logger.error(
                "Failed to load transport plugin '%s': %s",
                ep.name,
                e,
                exc_info=True,
            )

    if discovered > 0:
        logger.info("Discovered %d transport plugin(s)", discovered)
    else:
        logger.debug("No transport plugins found")

    return discovered


def get_transport_stats() -> dict[str, Any]:
    """
    Get statistics about registered transports.

    Returns:
        Dictionary with transport counts and details
    """
    builtin = [t for t in _TRANSPORTS.values() if t.source == TransportSource.BUILTIN]
    plugins = [t for t in _TRANSPORTS.values() if t.source == TransportSource.PLUGIN]

    return {
        "total": len(_TRANSPORTS),
        "builtin_count": len(builtin),
        "plugin_count": len(plugins),
        "transports": {
            name: {
                "source": defn.source.value,
                "input_codec": defn.input_codec,
                "input_sample_rate": defn.input_sample_rate,
                "output_codec": defn.output_codec,
                "output_sample_rate": defn.output_sample_rate,
                "bidirectional": defn.supports_bidirectional,
            }
            for name, defn in _TRANSPORTS.items()
        },
    }


__all__ = [
    "TransportSource",
    "TransportDefinition",
    "register_transport",
    "unregister_transport",
    "get_transport",
    "get_all_transports",
    "list_transports",
    "list_transport_names",
    "clear_transports",
    "discover_plugins",
    "get_plugin_routers",
    "get_transport_stats",
]
