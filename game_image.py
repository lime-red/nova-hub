#!/usr/bin/env python3
"""
Game images, and which one each league's game runs.

An Immortal Barons league runs the binary of the image its league_games row
names. To move a league to a new release:

    .venv/bin/python game_image.py import-ib /path/to/v0.2.7/immortal-barons --version v0.2.7
    .venv/bin/python game_image.py set 015I ib-v0.2.7
    .venv/bin/python game_image.py list

`set` takes effect at the league's next run (the hub need not be restarted)
and runs preflight on the league. An image another league still uses is kept.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.core.config import init_config
from backend.core.database import get_session, init_database
from backend.models.database import GameImage, League
from backend.services import preflight
from backend.services.game_images import import_ib_image
from backend.services.league_utils import parse_league_id


def _league(db, full_id: str) -> League:
    try:
        number, letter = parse_league_id(full_id)
    except Exception:
        raise SystemExit(f"{full_id!r} is not a league, as 015I")
    league = db.query(League).filter(League.league_id == number,
                                     League.game_type == letter).first()
    if league is None:
        raise SystemExit(f"No league {full_id}")
    return league


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the images, and the leagues using each")
    p = sub.add_parser("import-ib", help="copy an Immortal Barons binary in as an image")
    p.add_argument("binary")
    p.add_argument("--version", required=True, help="as v0.2.7; the image is ib-<version>")
    p = sub.add_parser("set", help="make a league's game run an image")
    p.add_argument("league", help="as 015I")
    p.add_argument("image", help="the image's folder name, as ib-v0.2.7")
    args = parser.parse_args(argv)

    config = init_config(args.config)
    init_database(f"sqlite:///{config.database.path}")
    db = get_session()
    try:
        if args.command == "list":
            for image in db.query(GameImage).order_by(GameImage.dirname):
                users = sorted(g.league.full_id for g in image.games)
                print(f"{image.dirname:24} {image.game_type} {image.version:12} "
                      f"{', '.join(users) or '(unused)'}")
            return 0

        if args.command == "import-ib":
            try:
                image, created = import_ib_image(db, config._raw, Path(args.binary), args.version)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 1
            db.commit()
            print(f"{'Imported' if created else 'Already there:'} {image.dirname}")
            return 0

        league = _league(db, args.league)
        image = db.query(GameImage).filter(GameImage.dirname == args.image).first()
        if image is None:
            print(f"No image {args.image}", file=sys.stderr)
            return 1
        if league.install is None:
            print(f"The hub has no game for {league.full_id}", file=sys.stderr)
            return 1
        if image.game_type != league.game_type:
            print(f"{image.dirname} is a {image.game_type} image; {league.full_id} is not",
                  file=sys.stderr)
            return 1
        was = league.install.image.dirname if league.install.image else None
        league.install.image_id = image.id
        db.commit()
        db.refresh(league)
        print(f"{league.full_id}: {was} -> {image.dirname}")
        problems = preflight.run(db, config._raw, [league])[league.full_id]
        print("preflight: " + ("ok" if not problems else "; ".join(problems)))
        return 0 if not problems else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
