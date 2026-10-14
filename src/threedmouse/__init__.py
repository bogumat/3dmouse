"""threedmouse — read a 3Dconnexion SpaceMouse's 6-DoF output directly over HID.

Reverse-engineered, driverless 6-DoF (+ buttons) from a 3Dconnexion SpaceMouse,
exposed as a small library for driving 3D objects, cameras and robots, plus a
``3dmouse`` CLI.

    from threedmouse import SpaceMouse

    with SpaceMouse() as mouse:
        while True:
            s = mouse.state            # latest sample (non-blocking)
            print(s.x, s.y, s.z, s.a, s.b, s.c, s.left, s.right)
"""
from .core import (
    SpaceMouse,
    State,
    DEFAULT_VID,
    DEFAULT_PID,
    DEFAULT_FULL_SCALE,
)

__all__ = ["SpaceMouse", "State", "DEFAULT_VID", "DEFAULT_PID", "DEFAULT_FULL_SCALE"]
__version__ = "0.2.0"
