#!/usr/bin/env python3
"""
Make a golden image: a clean, finished install of one game version, packed as
<dirname>.tar.gz with a manifest of every file and folder in it. A hub takes it
on its Images page (upload), from its images folder (drop-in), or by fetch.

    .venv/bin/python make_golden.py bre /path/to/bre_sj1.zip
    .venv/bin/python make_golden.py fe  /path/to/fe_sj1.zip --version sj1-r2
    .venv/bin/python make_golden.py ib  /path/to/immortal-barons --version v0.2.6

BRE and FE need dosemu on this machine: the media is cleaned of its donor's
league inside your drive_c, GAME/ is regenerated with BREDATA/FEDATA -Y, and a
copy is RESET as a throwaway league to show the image makes a game. The media
is your own copy of the game; it carries registration keys, so keep the images
somewhere only your hubs can read.

What goes into an image, and why, is in backend/services/golden.py.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.services.game_images import ImageError
from backend.services.golden import RECIPE, Dosemu, GoldenError, build_dos_image, build_ib_image


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("game", choices=("bre", "fe", "ib"))
    parser.add_argument("source", help="the game's media (.zip), or for ib its binary")
    parser.add_argument("--version", help=f"the image's version; for bre and fe the media's "
                                          f"name and the recipe, as sj1-r{RECIPE}, by default")
    parser.add_argument("--out", default=".", help="where to write the archive (default: here)")
    parser.add_argument("--drive-c", default=str(Path.home() / ".dosemu" / "drive_c"),
                        help="your dosemu C: (default: ~/.dosemu/drive_c)")
    parser.add_argument("--dosemu", default="/usr/bin/dosemu")
    parser.add_argument("--dosemu-conf", help="a dosemu config file to run with (-f)")
    parser.add_argument("--log-dir", default=".", help="where the dosemu transcripts go")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        print(f"{source} is not a file", file=sys.stderr)
        return 1
    try:
        if args.game == "ib":
            if not args.version:
                print("an ib image needs --version, as v0.2.6", file=sys.stderr)
                return 1
            archive = build_ib_image(source, args.version, Path(args.out))
        else:
            dos = Dosemu(drive_c=Path(args.drive_c), dosemu=args.dosemu,
                         conf=Path(args.dosemu_conf) if args.dosemu_conf else None,
                         log_dir=Path(args.log_dir))
            if not dos.drive_c.is_dir():
                print(f"{dos.drive_c} is not a folder; give your dosemu C: with --drive-c",
                      file=sys.stderr)
                return 1
            archive = build_dos_image({"bre": "B", "fe": "F"}[args.game], source, Path(args.out),
                                      dos, version=args.version)
    except (GoldenError, ImageError) as e:
        print(f"No image made: {e}", file=sys.stderr)
        return 1
    print(f"Made {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
