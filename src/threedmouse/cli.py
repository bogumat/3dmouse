"""Command-line entry point: stream SpaceMouse 6-DoF as CSV on stdout."""
from __future__ import annotations

import argparse
import signal
import sys
import time

from .core import SpaceMouse, DEFAULT_VID, DEFAULT_PID, DEFAULT_FULL_SCALE


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="3dmouse",
        description="Stream 6-DoF SpaceMouse output as CSV: x,y,z,a,b,c,left,right")
    ap.add_argument("--raw", action="store_true",
                    help="emit raw int16 device values instead of normalized [-1,1]")
    ap.add_argument("--no-idle", action="store_true",
                    help="print only on activity (suppress fully-at-rest lines)")
    ap.add_argument("--no-buttons", action="store_true",
                    help="drop the two trailing button-state fields (left,right)")
    ap.add_argument("--rate", type=float, default=125.0,
                    help="max output samples per second (default 125)")
    ap.add_argument("--precision", type=int, default=4,
                    help="decimals for normalized output (default 4)")
    ap.add_argument("--vid", type=lambda s: int(s, 0), default=DEFAULT_VID,
                    help=f"USB vendor id (default {DEFAULT_VID:#06x})")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=DEFAULT_PID,
                    help=f"USB product id (default {DEFAULT_PID:#06x})")
    ap.add_argument("--full-scale", type=float, default=DEFAULT_FULL_SCALE,
                    help=f"raw value at full deflection (default {DEFAULT_FULL_SCALE:g})")
    ap.add_argument("--no-takeover", action="store_true",
                    help="do not stop the 3Dconnexion driver (use if the device is already free)")
    return ap


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    def _bail(*_):
        raise SystemExit(0)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _bail)

    prec = args.precision

    def fmt(s) -> str:
        if args.raw:
            line = ",".join(str(v) for v in s.raw)
        else:
            line = ",".join(f"{v:+.{prec}f}" for v in s.normalized)
        if not args.no_buttons:
            line += f",{int(s.left)},{int(s.right)}"
        return line

    mouse = SpaceMouse(vid=args.vid, pid=args.pid, full_scale=args.full_scale,
                       takeover=(False if args.no_takeover else "auto"))
    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    last = 0.0
    ret = 0
    try:
        mouse.open()
        print(f"[3dmouse] streaming {'raw int16' if args.raw else 'normalized [-1,1]'} "
              f"— Ctrl-C to stop.", file=sys.stderr)
        for s in mouse.stream():
            if args.no_idle and not s.moving and s.buttons == 0:
                continue
            now = time.monotonic()
            if now - last < interval:
                continue
            last = now
            sys.stdout.write(fmt(s) + "\n")
            sys.stdout.flush()
    except RuntimeError as e:
        print(f"[3dmouse] {e}", file=sys.stderr)
        ret = 2
    except (KeyboardInterrupt, SystemExit):
        pass
    except BrokenPipeError:
        pass
    finally:
        took_over = mouse._took_over
        mouse.close()
        if took_over:
            print("[3dmouse] 3Dconnexion driver restored.", file=sys.stderr)
    return ret


if __name__ == "__main__":
    raise SystemExit(main())
