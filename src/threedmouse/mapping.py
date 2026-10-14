"""Shape and route SpaceMouse output to arbitrary targets.

:class:`Mapper` turns a raw :class:`~threedmouse.State` into shaped values and
fires button callbacks. It is configured with plain data (kwargs, a dict, or a
JSON/TOML file), so you can tune behaviour without touching code.

Per axis you can set: ``source`` (read a different axis — remap), ``invert``,
``deadzone``, ``curve`` (expo; >1 = finer near centre), ``gain``, and ``clamp``.
Global defaults apply to every axis unless an ``axes`` entry overrides them.

    from threedmouse import SpaceMouse, Mapper

    mapper = Mapper(
        deadzone=0.08,                       # applied to all axes
        axes={
            "x": {"gain": 2.0},              # faster pan
            "c": {"curve": 1.6},             # gentle yaw near centre
            "z": {"source": "y", "invert": True},  # feed device Y into Z, flipped
        },
        buttons={
            "left":  {"on_press": lambda: print("grip")},
            "right": {"on_hold":  lambda: print("boost")},
        },
    )

    with SpaceMouse() as m:
        for s in m.stream():
            out = mapper.apply(s)            # out.x, out.y, …; fires button callbacks
            robot.twist(*out.values)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

#: The six output axis names, in order.
AXES: Tuple[str, ...] = ("x", "y", "z", "a", "b", "c")


@dataclass
class AxisSpec:
    """Shaping for one output axis. ``source`` reads a different input axis."""
    source: Optional[str] = None
    invert: bool = False
    deadzone: float = 0.0
    curve: float = 1.0
    gain: float = 1.0
    clamp: Optional[Tuple[float, float]] = None

    def shape(self, v: float) -> float:
        if self.invert:
            v = -v
        dz = self.deadzone
        if dz > 0.0:
            if abs(v) <= dz:
                return 0.0
            v = math.copysign((abs(v) - dz) / (1.0 - dz), v)
        if self.curve != 1.0:
            v = math.copysign(abs(v) ** self.curve, v)
        v *= self.gain
        if self.clamp is not None:
            lo, hi = self.clamp
            v = lo if v < lo else hi if v > hi else v
        return v


@dataclass
class Mapped:
    """Result of :meth:`Mapper.apply` — shaped axes plus button states."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    left: bool = False
    right: bool = False

    @property
    def values(self) -> Tuple[float, float, float, float, float, float]:
        return (self.x, self.y, self.z, self.a, self.b, self.c)

    def __iter__(self):
        return iter(self.values)


_BTN_EVENTS = ("on_press", "on_release", "on_hold")


class Mapper:
    """Configurable shaping + button bindings for SpaceMouse output."""

    def __init__(
        self,
        *,
        deadzone: float = 0.0,
        curve: float = 1.0,
        gain: float = 1.0,
        invert: bool = False,
        clamp: Optional[Tuple[float, float]] = None,
        axes: Optional[Dict[str, Dict[str, Any]]] = None,
        buttons: Optional[Dict[str, Dict[str, Callable[[], None]]]] = None,
    ) -> None:
        base = dict(deadzone=deadzone, curve=curve, gain=gain, invert=invert, clamp=clamp)
        axes = axes or {}
        unknown = set(axes) - set(AXES)
        if unknown:
            raise ValueError(f"unknown axis name(s) {sorted(unknown)}; valid: {list(AXES)}")
        self.specs: Dict[str, AxisSpec] = {}
        for name in AXES:
            cfg = dict(base)
            cfg.update(axes.get(name, {}))
            src = cfg.pop("source", None)
            if src is not None and src not in AXES:
                raise ValueError(f"axis {name!r} source {src!r} is not one of {list(AXES)}")
            clamp_val = cfg.get("clamp")
            if clamp_val is not None:
                cfg["clamp"] = tuple(clamp_val)
            self.specs[name] = AxisSpec(source=src, **cfg)
        self.buttons = buttons or {}
        for bname, binds in self.buttons.items():
            if bname not in ("left", "right"):
                raise ValueError(f"unknown button {bname!r}; valid: ['left', 'right']")
            bad = set(binds) - set(_BTN_EVENTS)
            if bad:
                raise ValueError(f"button {bname!r}: unknown event(s) {sorted(bad)}; "
                                 f"valid: {list(_BTN_EVENTS)}")
        self._prev = {"left": False, "right": False}

    def apply(self, state) -> Mapped:
        """Shape ``state`` into a :class:`Mapped`, firing any button callbacks."""
        out = Mapped(left=state.left, right=state.right)
        for name in AXES:
            spec = self.specs[name]
            setattr(out, name, spec.shape(getattr(state, spec.source or name)))
        for bname in ("left", "right"):
            cur = bool(getattr(state, bname))
            prev = self._prev[bname]
            binds = self.buttons.get(bname, {})
            if cur and not prev and "on_press" in binds:
                binds["on_press"]()
            elif not cur and prev and "on_release" in binds:
                binds["on_release"]()
            if cur and "on_hold" in binds:
                binds["on_hold"]()
            self._prev[bname] = cur
        return out

    __call__ = apply

    # ---- easy configuration from data ------------------------------------
    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "Mapper":
        """Build from a plain dict (same keys as the constructor)."""
        return cls(**config)

    @classmethod
    def from_file(cls, path: str) -> "Mapper":
        """Load shaping config from a ``.json`` or ``.toml`` file.

        (Button callbacks can't be serialized — set those in code.)
        """
        text = open(path, "r", encoding="utf-8").read()
        if str(path).lower().endswith(".toml"):
            try:
                import tomllib  # Python 3.11+
            except ModuleNotFoundError as e:
                raise RuntimeError("TOML config needs Python 3.11+; use JSON instead") from e
            data = tomllib.loads(text)
        else:
            data = json.loads(text)
        return cls.from_dict(data)
