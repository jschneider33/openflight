"""GSPro TCP client — connection thread + state machine + reconnect."""
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from openflight.gspro.config import GSProConfig
from openflight.gspro.messages import build_heartbeat, GSProResponse, parse_response
from openflight.gspro.state import ConnectionState

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF: Tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


@dataclass
class StatusEvent:
    state: ConnectionState
    host: str = ""
    port: int = 0
    attempt: int = 0
    next_retry_in_s: float = 0.0
    message: str = ""


class GSProClient:
    """TCP client with reconnect.

    Lifecycle:
      start() — spawn connection thread; transitions DISABLED → CONNECTING → CONNECTED.
      stop()  — terminate thread; transitions to STOPPED.

    Callbacks (set as attributes):
      on_response(GSProResponse) — called per received reply
      on_status(StatusEvent)     — called on every state change
    """

    def __init__(
        self,
        config: GSProConfig,
        on_response: Optional[Callable[[GSProResponse], None]] = None,
        on_status: Optional[Callable[["StatusEvent"], None]] = None,
        backoff_seconds: Tuple[float, ...] = DEFAULT_BACKOFF,
    ):
        self._config = config
        self.on_response = on_response
        self.on_status = on_status
        self._backoff = backoff_seconds
        self._state = ConnectionState.DISABLED
        self._state_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._conn_thread: Optional[threading.Thread] = None
        self._hb_thread: Optional[threading.Thread] = None
        self._last_send_time = 0.0
        self._send_time_lock = threading.Lock()

    # --- public state ---------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        with self._state_lock:
            return self._state

    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    # --- start / stop ---------------------------------------------------------

    def start(self) -> None:
        if self._conn_thread is not None and self._conn_thread.is_alive():
            return
        self._stop_event.clear()
        self._conn_thread = threading.Thread(
            target=self._connection_loop, name="gspro-conn", daemon=True,
        )
        self._conn_thread.start()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="gspro-hb", daemon=True,
        )
        self._hb_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._close_socket()
        for t in (self._conn_thread, self._hb_thread):
            if t is not None:
                t.join(timeout=3.0)
        self._conn_thread = None
        self._hb_thread = None
        self._set_state(ConnectionState.STOPPED)

    # --- send -----------------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        with self._sock_lock:
            if self._sock is None:
                raise RuntimeError("send_raw called while not connected")
            self._sock.sendall(data)
        with self._send_time_lock:
            self._last_send_time = time.time()

    # --- internals ------------------------------------------------------------

    def _send_heartbeat(self) -> None:
        # Use a separate path that does NOT update _last_send_time, otherwise
        # heartbeats would keep themselves alive even when no real traffic.
        with self._sock_lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(build_heartbeat(
                    self._config.device_id, self._config.units,
                    shot_number=0,
                ))
            except OSError as e:
                logger.info("[gspro] heartbeat send failed: %s", e)

    def _heartbeat_loop(self) -> None:
        interval = max(self._config.heartbeat_interval_s, 0.05)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                return
            if self.state != ConnectionState.CONNECTED:
                continue
            with self._send_time_lock:
                idle_for = time.time() - self._last_send_time
            if idle_for < interval:
                continue
            self._send_heartbeat()

    def _set_state(self, new_state: ConnectionState, **status_kwargs) -> None:
        with self._state_lock:
            if self._state == new_state and not status_kwargs:
                return
            self._state = new_state
        if self.on_status is not None:
            self.on_status(StatusEvent(
                state=new_state,
                host=self._config.host,
                port=self._config.port,
                **status_kwargs,
            ))

    def _close_socket(self) -> None:
        with self._sock_lock:
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

    def _try_connect(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        try:
            s.connect((self._config.host, self._config.port))
        except OSError as e:
            try:
                s.close()
            except OSError:
                pass
            logger.info("[gspro] connect failed: %s", e)
            return False
        with self._sock_lock:
            self._sock = s
        return True

    def _backoff_for_attempt(self, attempt: int) -> float:
        idx = min(attempt, len(self._backoff) - 1)
        return self._backoff[idx]

    def _connection_loop(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            self._set_state(ConnectionState.CONNECTING)
            if self._try_connect():
                attempt = 0
                self._set_state(ConnectionState.CONNECTED)
                self._recv_loop()
                self._close_socket()
                if self._stop_event.is_set():
                    break
                # Connection dropped — fall through to reconnect
            wait = self._backoff_for_attempt(attempt)
            self._set_state(
                ConnectionState.RECONNECT_BACKOFF,
                attempt=attempt + 1, next_retry_in_s=wait,
            )
            attempt += 1
            self._stop_event.wait(timeout=wait)

    def _recv_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._sock_lock:
                sock = self._sock
            if sock is None:
                return
            sock.settimeout(0.2)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            try:
                response = parse_response(data)
            except ValueError as e:
                logger.warning("[gspro] dropping malformed response: %s", e)
                continue
            if self.on_response is not None:
                try:
                    self.on_response(response)
                except Exception:  # pylint: disable=broad-except
                    logger.exception("[gspro] on_response raised")
