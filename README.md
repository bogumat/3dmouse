# 3dmouse

Read **6-DoF motion + buttons** from a 3Dconnexion SpaceMouse on macOS — a small
Python library for driving 3D views, cameras and robots, plus a `3dmouse` CLI
that streams CSV.

```
$ 3dmouse
+0.2057,+0.1086,+0.0629,+0.0000,-0.3857,+0.1771,0,0
+0.0000,+0.0000,+0.0000,+0.0000,+0.0000,+0.0000,1,0   # left button held
```

Fields: `x,y,z,a,b,c,left,right`

- `x, y, z` — translation (push / pull / lift)
- `a, b, c` — rotation `rx, ry, rz` (tilt / twist)
- `left, right` — button states (`0`/`1`)

Axes are normalized to `[-1, 1]` (full-scale deflection ≈ ±350 raw units).

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

## CLI

```
3dmouse                 # normalized [-1,1], one line per sample
3dmouse --raw           # raw signed int16 values
3dmouse --no-idle       # only print on activity
3dmouse --no-buttons    # drop the two button fields
3dmouse --rate 60       # cap output rate (default 125 Hz)
3dmouse --precision 3   # decimals for normalized output
3dmouse --no-takeover   # don't stop the driver (if the device is already free)
3dmouse --vid 0x256f --pid 0xc635 --full-scale 350
```

`Ctrl-C` to stop. Example: `3dmouse --no-idle | while IFS=, read x y z a b c l r; do …; done`

## How it works

On macOS the 3DxWare driver holds the SpaceMouse open and only routes it to the
foreground GUI app, so a terminal tool can't read it through the normal
channels. `3dmouse` therefore stops the driver's `3DconnexionHelper`, reads the
device's raw HID reports directly (the layout below, reverse engineered), and
relaunches the helper on exit — on `close()`, `Ctrl-C`, `SIGTERM`, or process
exit. No admin required and nothing permanent changes; while `3dmouse` runs, the
official 3DxWare apps don't receive input.

```
report id 0x01 -> Tx, Ty, Tz        (3× signed int16 LE, translation)
report id 0x02 -> Rx, Ry, Rz        (rotation)
report id 0x03 -> button bitmask    (bit 0 = left, bit 1 = right)
```

Tested with a **SpaceMouse Compact** (`VID 0x256F / PID 0xC635`). Other models
share the report layout but use different PIDs — pass `--pid` / `pid=`.

## Development

```sh
git clone https://github.com/bogumat/3dmouse
cd 3dmouse
uv sync
uv run 3dmouse
```

If a `3dmouse` process is killed with `SIGKILL` (bypassing cleanup), restore the
driver manually: `open /Applications/3DconnexionHelper.app`.

## License

MIT
