# 3dmouse

Read **6-DoF motion + buttons** from a 3Dconnexion SpaceMouse — a small Python
library for driving 3D views, cameras and robots, plus a `3dmouse` CLI that
streams CSV. Works on macOS, Linux and Windows (see
[Platform support](#platform-support)).

```
$ 3dmouse
+0.2057,+0.1086,+0.0629,+0.0000,-0.3857,+0.1771,0,0
+0.0000,+0.0000,+0.0000,+0.0000,+0.0000,+0.0000,1,0   # left button held
```

Fields: `x,y,z,a,b,c,left,right`, normalized to `[-1, 1]` (full-scale ≈ ±350).

Axes use a calibrated right-handed convention:

| axis | `+` direction |
|---|---|
| `x` | slide **right** |
| `y` | slide **forward / away** |
| `z` | lift **up** |
| `a` | pitch — **front edge down** |
| `b` | roll — **right edge down** |
| `c` | yaw — **twist left** (CCW from above) |

`left, right` are the two button states (`0`/`1`).

## Install

```sh
uv tool install git+https://github.com/bogumat/3dmouse     # the `3dmouse` CLI
uvx --from git+https://github.com/bogumat/3dmouse 3dmouse   # run without installing
```

As a dependency in another project:

```sh
uv add "threedmouse @ git+https://github.com/bogumat/3dmouse"
```

`hidapi` is installed automatically. The import name is `threedmouse`.

## Library

A background thread reads the device at ~125 Hz and keeps the latest sample in a
latch, so your control loop polls at *its own* rate:

```python
import time
from threedmouse import SpaceMouse

with SpaceMouse() as mouse:          # takes over the device; restores on exit
    while True:
        s = mouse.state             # non-blocking, always the latest sample
        robot.twist(s.x, s.y, s.z,  # translation, each in [-1, 1]
                    s.a, s.b, s.c)  # rotation,    each in [-1, 1]
        if s.left:
            robot.grip()
        time.sleep(1 / 100)
```

Or iterate every device event, or use a callback:

```python
with SpaceMouse() as mouse:
    for s in mouse.stream():
        print(s.x, s.y, s.z, s.a, s.b, s.c, s.left, s.right)

SpaceMouse(on_event=lambda s: ...).open()
```

### `State`

| attribute | meaning |
|---|---|
| `x, y, z` / `a, b, c` | translation / rotation, normalized `[-1, 1]` |
| `tx, ty, tz, rx, ry, rz` | raw signed int16 (≈ ±350) |
| `left`, `right` | `bool` button states |
| `translation`, `rotation` | `(x, y, z)` / `(a, b, c)` tuples |
| `raw`, `normalized` | the two six-tuples |
| `moving` | any axis non-zero |

### `SpaceMouse(**opts)`

Options: `vid`, `pid`, `full_scale`, `takeover` (`"auto"` | `True` | `False`),
`on_event`, `queue_size`, `open_timeout`. Methods: `open()`, `close()`, context
manager, `state` / `read()` (latch), `stream()` (generator).

## Mapping — shape & route outputs

`Mapper` reshapes each axis and binds buttons to callbacks, configured with
plain data (kwargs, a dict, or a JSON/TOML file) so you can tune it without
editing code.

```python
from threedmouse import SpaceMouse, Mapper

mapper = Mapper(
    deadzone=0.08,                              # applied to every axis
    axes={
        "x": {"gain": 2.0},                     # faster pan
        "c": {"curve": 1.6},                    # finer yaw near centre
        "z": {"source": "y", "invert": True},   # feed device Y into Z, flipped
    },
    buttons={
        "left":  {"on_press": lambda: robot.grip()},
        "right": {"on_hold":  lambda: robot.boost()},
    },
)

with SpaceMouse() as m:
    for s in m.stream():
        out = mapper.apply(s)       # out.x … out.c shaped; fires button callbacks
        robot.twist(*out.values)
```

Per-axis options (global defaults + per-axis overrides in `axes`):

| option | effect |
|---|---|
| `source` | read a different input axis (remap), e.g. `"z": {"source": "y"}` |
| `invert` | flip sign |
| `deadzone` | ignore inputs below this magnitude, rescaling the rest to `[-1,1]` |
| `curve` | response exponent; `>1` = finer near centre |
| `gain` | scale (may exceed 1 for velocity targets) |
| `clamp` | `[lo, hi]` limit after gain |

Button events: `on_press`, `on_release`, `on_hold`.

Load shaping from a file (axes only — set button callbacks in code):

```python
mapper = Mapper.from_file("mapping.toml")
```

```toml
# mapping.toml
deadzone = 0.08
[axes.x]
gain = 2.0
[axes.c]
curve = 1.6
```

The CLI applies the same file with `--config`:

```sh
3dmouse --config mapping.toml
```

## CLI

```
3dmouse                 # normalized [-1,1], one line per sample
3dmouse --raw           # raw signed int16 values
3dmouse --no-idle       # only print on activity
3dmouse --no-buttons    # drop the two button fields
3dmouse --rate 60       # cap output rate (default 125 Hz)
3dmouse --precision 3   # decimals for normalized output
3dmouse --no-takeover   # don't stop the driver (if the device is already free)
3dmouse --config m.toml # shape axes via a JSON/TOML mapping file (see Mapping)
3dmouse --vid 0x256f --pid 0xc635 --full-scale 350
```

`Ctrl-C` to stop. Example: `3dmouse --no-idle | while IFS=, read x y z a b c l r; do …; done`

## How it works

It reads the device's raw HID input reports directly and decodes them (layout
below, reverse engineered):

```
report id 0x01 -> Tx, Ty, Tz        (3× signed int16 LE, translation)
report id 0x02 -> Rx, Ry, Rz        (rotation)
report id 0x03 -> button bitmask    (bit 0 = left, bit 1 = right)
```

Tested with a **SpaceMouse Compact** (`VID 0x256F / PID 0xC635`). Other models
share the report layout but use different PIDs — pass `--pid` / `pid=`.

## Platform support

The reading is cross-platform (via `hidapi`). The one OS-specific wrinkle is
getting exclusive access to the device:

- **macOS** — the 3DxWare driver holds the SpaceMouse open and only routes it to
  the foreground app, so `3dmouse` stops the driver's `3DconnexionHelper` while
  running and relaunches it on exit (`close()`, `Ctrl-C`, `SIGTERM`, or process
  exit). No admin needed; nothing permanent changes. Disable with
  `--no-takeover` / `takeover=False`.
- **Linux** — reads `/dev/hidraw*` directly; no take-over. You may need read
  permission (a udev rule for the device, or run as root). `spacenavd` does not
  need to be running.
- **Windows** — reads the HID device directly alongside the 3DxWare driver; no
  take-over.

> Developed and verified on macOS with a SpaceMouse Compact. Linux/Windows use
> the same code path (hidapi read + the report layout above) but have not been
> hardware-tested here.

## Development

```sh
git clone https://github.com/bogumat/3dmouse
cd 3dmouse
uv sync
uv run 3dmouse
```

If a `3dmouse` process is killed with `SIGKILL` (bypassing cleanup) on macOS,
restore the driver manually: `open /Applications/3DconnexionHelper.app`.

## License

MIT
