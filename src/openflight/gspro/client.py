"""GSPro TCP client — synchronous primitives.

Threading (state machine + heartbeat) is added in subsequent tasks.
"""
import logging
import socket
from typing import Callable, Optional

from openflight.gspro.config import GSProConfig
from openflight.gspro.messages import GSProResponse, parse_response

logger = logging.getLogger(__name__)


class GSProClient:
    """TCP client for GSPro OpenConnectV1.

    Public API:
      connect() / close() / is_connected()
      send_raw(bytes)
      poll(timeout) — synchronous read; dispatches via on_response callback
    """

    def __init__(
        self,
        config: GSProConfig,
        on_response: Optional[Callable[[GSProResponse], None]] = None,
    ):
        self._config = config
        self._on_response = on_response
        self._sock: Optional[socket.socket] = None

    # --- connection lifecycle -------------------------------------------------

    def connect(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((self._config.host, self._config.port))
        self._sock = s
        logger.info("[gspro] connected to %s:%d", self._config.host, self._config.port)

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
        logger.info("[gspro] disconnected")

    def is_connected(self) -> bool:
        return self._sock is not None

    # --- I/O ------------------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        if self._sock is None:
            raise RuntimeError("send_raw called while not connected")
        self._sock.sendall(data)

    def poll(self, timeout: float = 0.1) -> None:
        """Read once from socket and dispatch response if any."""
        if self._sock is None:
            return
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(4096)
        except socket.timeout:
            return
        if not data:
            self.close()
            return
        try:
            response = parse_response(data)
        except ValueError as e:
            logger.warning("[gspro] dropping malformed response: %s", e)
            return
        if self._on_response is not None:
            self._on_response(response)
