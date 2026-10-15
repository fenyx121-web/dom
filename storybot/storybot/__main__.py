import argparse
import logging
from pathlib import Path

from .config import load_config
from .pipeline import make_stories
from .watch import watch


def main() -> None:
    ap = argparse.ArgumentParser(prog="storybot",
                                 description="Auto-edit glasses footage into Instagram stories")
    ap.add_argument("--config", help="path to config.json (default ./config.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("watch", help="watch the inbox folder and process new clips")
    r = sub.add_parser("run", help="process given video files once")
    r.add_argument("files", nargs="+")
    r.add_argument("--out", help="output folder (default <out>/manual)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    if args.cmd == "watch":
        watch(cfg)
    else:
        outs = make_stories(args.files, Path(args.out or Path(cfg.out) / "manual"), cfg)
        for o in outs:
            print(o)


main()
