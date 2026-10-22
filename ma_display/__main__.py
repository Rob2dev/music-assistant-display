"""Entry point: python -m ma_display"""
import argparse
import logging

from .app import App
from .calibrate import calibrate
from .config import load_config
from .hardware import PngSink, open_framebuffer
from .i18n import set_language


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--png")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiosendspin").setLevel(logging.WARNING)
    cfg = load_config()
    set_language(cfg["language"])
    sink = PngSink(args.png) if args.png else open_framebuffer(cfg)
    try:
        if args.calibrate:
            calibrate(cfg, sink)
            return
        App(cfg, sink, demo=args.demo).run(once=bool(args.png))
    finally:
        sink.close()


if __name__ == "__main__":
    main()
