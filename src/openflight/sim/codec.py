"""SimConnector: pairs a codec with a TCP transport, and the codec registry.

A connector is the unit the server fans shots out to. Adding a new simulator
means writing a codec and registering it here — nothing else in the server or
transport changes.
"""

import logging
from typing import Callable, Optional

from openflight.sim.transport import DEFAULT_BACKOFF, Codec, TcpSimClient
from openflight.sim.types import InboundEvent, ResolvedShot, StatusEvent

logger = logging.getLogger(__name__)


class SimConnector:
    """One simulator endpoint: codec + transport + per-target callback routing.

    Callbacks are invoked as ``on_status(target, StatusEvent)`` and
    ``on_inbound(target, InboundEvent)`` so the server can multiplex several
    connectors through a single pair of handlers.
    """

    def __init__(
        self,
        codec: Codec,
        host: str,
        port: int,
        heartbeat_interval_s: float = 5.0,
        on_status: Optional[Callable[[str, StatusEvent], None]] = None,
        on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
        backoff_seconds=DEFAULT_BACKOFF,
    ):
        self.codec = codec
        self.name = codec.name
        self.host = host
        self.port = port
        self._on_status_user = on_status
        self._on_inbound_user = on_inbound
        self._client = TcpSimClient(
            host=host,
            port=port,
            codec=codec,
            heartbeat_interval_s=heartbeat_interval_s,
            name=codec.name,
            on_inbound=self._handle_inbound,
            on_status=self._handle_status,
            backoff_seconds=backoff_seconds,
        )

    def _handle_status(self, event: StatusEvent) -> None:
        if self._on_status_user is not None:
            self._on_status_user(self.name, event)

    def _handle_inbound(self, event: InboundEvent) -> None:
        if self._on_inbound_user is not None:
            self._on_inbound_user(self.name, event)

    def start(self) -> None:
        self._client.start()

    def stop(self) -> None:
        self._client.stop()

    def is_connected(self) -> bool:
        return self._client.is_connected()

    @property
    def state(self):
        return self._client.state

    def send_shot(self, resolved: ResolvedShot) -> None:
        """Serialize and send a resolved shot. Raises OSError if the socket fails."""
        self._client.send_raw(self.codec.build_shot(resolved))


def _codec_for(connector_type: str, cfg: dict) -> Codec:
    """Instantiate the codec for a connector type. Imports are local to avoid
    an import cycle (codecs import sim.types/resolver)."""
    if connector_type == "gspro":
        from openflight.gspro.codec import GSProCodec  # pylint: disable=import-outside-toplevel

        return GSProCodec(
            device_id=cfg.get("device_id", "OpenFlight"),
            units=cfg.get("units", "Yards"),
        )
    if connector_type == "opengolfsim":
        from openflight.opengolfsim.codec import (
            OpenGolfSimCodec,  # pylint: disable=import-outside-toplevel
        )

        return OpenGolfSimCodec(units=cfg.get("units", "imperial"))
    raise ValueError(f"unknown simulator connector type: {connector_type!r}")


def build_connector(
    cfg: dict,
    on_status: Optional[Callable[[str, StatusEvent], None]] = None,
    on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
    backoff_seconds=DEFAULT_BACKOFF,
) -> SimConnector:
    """Build a single connector from a resolved connector-config dict."""
    codec = _codec_for(cfg["type"], cfg)
    return SimConnector(
        codec=codec,
        host=cfg.get("host", "127.0.0.1"),
        port=cfg["port"],
        heartbeat_interval_s=cfg.get("heartbeat_interval_s", 5.0),
        on_status=on_status,
        on_inbound=on_inbound,
        backoff_seconds=backoff_seconds,
    )
