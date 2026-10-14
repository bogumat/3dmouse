"""Core library: take over a 3Dconnexion SpaceMouse and stream 6-DoF + buttons.

The public surface is :class:`SpaceMouse` and :class:`State`.

Typical use in a control loop (poll the latch at your own rate)::

    from threedmouse import SpaceMouse

    with SpaceMouse() as mouse:          # takes over device; restores on exit
        while True:
            s = mouse.state              # latest sample, non-blocking
            robot.twist(s.x, s.y, s.z, s.a, s.b, s.c)
            time.sleep(1 / 100)

Or consume every device event::

    with SpaceMouse() as mouse:
        for s in mouse.stream():
            print(s.x, s.y, s.z, s.a, s.b, s.c, s.left, s.right)

Or register a callback::

    SpaceMouse(on_event=lambda s: ...).open()
"""
from __future__ import annotations

import atexit
import platform
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import hid

# SpaceMouse Compact; other models share the report layout but use other PIDs.
DEFAULT_VID = 0x256F
DEFAULT_PID = 0xC635
DEFAULT_FULL_SCALE = 350.0

# Axis-sign calibration (measured on a SpaceMouse Compact) that normalizes the
# raw device signs to an intuitive right-handed frame:
#   +x = slide right,  +y = slide forward/away,  +z = lift up
#   +a = pitch (front edge down),  +b = roll (right edge down),  +c = yaw (CCW from above)
# Order: tx, ty, tz, rx, ry, rz.
_SIGNS = (1, -1, -1, 1, -1, -1)

_HELPER_APP = "/Applications/3DconnexionHelper.app"
_HELPER_BIN = "3DconnexionHelper.app/Contents/MacOS/3DconnexionHelper"
_IS_MAC = platform.system() == "Darwin"


def _clamp(v: float) -> float:
    return 1.0 if v > 1.0 else -1.0 if v < -1.0 else v


@dataclass(frozen=True)
class State:
    """One 6-DoF + button sample, in the calibrated right-handed convention
    (+x right, +y forward, +z up; +a/+b/+c = pitch/roll/yaw).

    ``tx..rz`` are signed int16 (roughly ``±full_scale``); ``x, y, z, a, b, c``
    are those normalized to ``[-1, 1]``. ``a, b, c`` are the rotations
    ``rx, ry, rz``.
    """

    tx: int = 0
    ty: int = 0
    tz: int = 0
    rx: int = 0
    ry: int = 0
    rz: int = 0
    buttons: int = 0
    t: float = 0.0
    full_scale: float = DEFAULT_FULL_SCALE

    # normalized axes -------------------------------------------------------
    @property
    def x(self) -> float: return _clamp(self.tx / self.full_scale)
    @property
    def y(self) -> float: return _clamp(self.ty / self.full_scale)
    @property
    def z(self) -> float: return _clamp(self.tz / self.full_scale)
    @property
    def a(self) -> float: return _clamp(self.rx / self.full_scale)
    @property
    def b(self) -> float: return _clamp(self.ry / self.full_scale)
    @property
    def c(self) -> float: return _clamp(self.rz / self.full_scale)

    # buttons ---------------------------------------------------------------
    @property
    def left(self) -> bool: return bool(self.buttons & 0x1)
    @property
    def right(self) -> bool: return bool(self.buttons & 0x2)

    # convenience -----------------------------------------------------------
    @property
    def translation(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def rotation(self) -> tuple[float, float, float]:
        return (self.a, self.b, self.c)

    @property
    def raw(self) -> tuple[int, int, int, int, int, int]:
        return (self.tx, self.ty, self.tz, self.rx, self.ry, self.rz)

    @property
    def normalized(self) -> tuple[float, float, float, float, float, float]:
        return (self.x, self.y, self.z, self.a, self.b, self.c)

    @property
    def moving(self) -> bool:
        return any(self.raw)


# ---- 3Dconnexion driver (helper) control (macOS only) -----------------------
def _helper_running() -> bool:
    if not _IS_MAC:
        return False
    return subprocess.run(["pgrep", "-f", _HELPER_BIN],
                          stdout=subprocess.DEVNULL).returncode == 0


def _stop_helper() -> None:
    subprocess.run(["pkill", "-f", f"open -W {_HELPER_APP}"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", _HELPER_BIN], stderr=subprocess.DEVNULL)


def _start_helper() -> None:
    subprocess.run(["open", _HELPER_APP], stderr=subprocess.DEVNULL)


class SpaceMouse:
    """A 3Dconnexion SpaceMouse read directly over raw HID.

    On macOS the 3DxWare driver holds the device open, so by default (``takeover
    ="auto"``) this stops the driver's helper process while open and relaunches
    it on :meth:`close`. On other platforms, or if the device is already free,
    no take-over happens.
    """

    def __init__(
        self,
        *,
        vid: int = DEFAULT_VID,
        pid: int = DEFAULT_PID,
        full_scale: float = DEFAULT_FULL_SCALE,
        takeover: str | bool = "auto",
        on_event: Optional[Callable[[State], None]] = None,
        queue_size: int = 256,
        open_timeout: float = 8.0,
    ) -> None:
        self.vid = vid
        self.pid = pid
        self.full_scale = full_scale
        self.takeover = takeover
        self.on_event = on_event
        self.open_timeout = open_timeout

        self._dev: Optional["hid.device"] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._latch = State(full_scale=full_scale)
        self._q: "queue.Queue[State]" = queue.Queue(maxsize=queue_size)
        self._took_over = False
        self._closed = False

    # lifecycle -------------------------------------------------------------
    def open(self) -> "SpaceMouse":
        if self._dev is not None:
            return self
        self._closed = False
        want = self.takeover
        if want == "auto":
            want = _IS_MAC and _helper_running()
        # Arm restoration *before* taking over, so an interrupt or open failure
        # during the retry loop still relaunches the driver.
        atexit.register(self.close)
        if want:
            _stop_helper()
            self._took_over = True
            time.sleep(0.4)
        try:
            self._dev = self._open_hid()
        except BaseException:  # includes KeyboardInterrupt/SystemExit mid-retry
            self.close()
            raise
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="spacemouse", daemon=True)
        self._thread.start()
        return self

    def _open_hid(self) -> "hid.device":
        deadline = time.time() + self.open_timeout
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                d = hid.device()
                d.open(self.vid, self.pid)
                d.set_nonblocking(True)
                return d
            except Exception as e:  # held by the driver; retry while it releases
                last = e
                time.sleep(0.3)
        hint = ""
        if _IS_MAC and _helper_running():
            hint = " (the 3Dconnexion driver still holds it — takeover may have failed)"
        raise RuntimeError(f"could not open SpaceMouse {self.vid:#06x}:{self.pid:#06x}"
                           f"{hint}: {last}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        if self._took_over:
            _start_helper()
            self._took_over = False
        try:
            atexit.unregister(self.close)
        except Exception:
            pass

    def __enter__(self) -> "SpaceMouse":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # reader ----------------------------------------------------------------
    def _run(self) -> None:
        tx = ty = tz = rx = ry = rz = buttons = 0
        last_watchdog = time.monotonic()
        while not self._stop.is_set():
            try:
                r = self._dev.read(64)  # type: ignore[union-attr]
            except OSError:
                r = None
            if r:
                rid = r[0]
                if rid == 1 and len(r) >= 7:
                    tx = _SIGNS[0] * _s16(r[1], r[2])
                    ty = _SIGNS[1] * _s16(r[3], r[4])
                    tz = _SIGNS[2] * _s16(r[5], r[6])
                elif rid == 2 and len(r) >= 7:
                    rx = _SIGNS[3] * _s16(r[1], r[2])
                    ry = _SIGNS[4] * _s16(r[3], r[4])
                    rz = _SIGNS[5] * _s16(r[5], r[6])
                elif rid == 3:
                    buttons = int.from_bytes(bytes(r[1:]), "little")
                else:
                    continue
                st = State(tx, ty, tz, rx, ry, rz, buttons,
                           time.monotonic(), self.full_scale)
                self._latch = st
                self._offer(st)
                if self.on_event is not None:
                    try:
                        self.on_event(st)
                    except Exception:
                        pass
            else:
                time.sleep(0.001)

            # keep the device ours if the driver's helper respawns
            if self._took_over:
                now = time.monotonic()
                if now - last_watchdog > 1.5:
                    last_watchdog = now
                    if _helper_running():
                        _stop_helper()

    def _offer(self, st: State) -> None:
        try:
            self._q.put_nowait(st)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(st)
            except queue.Full:
                pass

    # consumer API ----------------------------------------------------------
    @property
    def state(self) -> State:
        """The most recent sample (non-blocking latch). Ideal for control loops."""
        return self._latch

    def read(self) -> State:
        """Alias for :attr:`state`."""
        return self._latch

    def stream(self, timeout: Optional[float] = None) -> Iterator[State]:
        """Yield each device event as it arrives (blocking). Ends when closed."""
        while not self._stop.is_set():
            try:
                yield self._q.get(timeout=timeout if timeout is not None else 0.2)
            except queue.Empty:
                if timeout is not None:
                    return
                continue


def _s16(lo: int, hi: int) -> int:
    v = lo | (hi << 8)
    return v - 0x10000 if v & 0x8000 else v
