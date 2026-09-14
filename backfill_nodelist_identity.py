#!/usr/bin/env python3
"""
Populate the nodelist identity columns added by e4a1c9b27d30.

NodelistGenerator now refuses to write a nodelist until it can emit the hub's
own node entry, which needs League.hub_fidonet_address. That column starts
NULL, so generation is blocked on every league until it is filled in.

Rather than invent the values, this reads them out of the nodes.dat the games
are already using -- the file the validator compares the database against, and
the one that has been correct all along -- and copies them into the database:

  League.hub_fidonet_address   <- the hub's own entry in that file
  League.hub_routes_mail       <- whether line 1 is "N HOST ..." or a bare "N"
  Client.city/state/country    <- each member's entry, matched by BBS index

It deliberately does NOT touch bbs_name. Where the file and the database
disagree on a name, that is a real decision about which one is right, and it is
reported rather than resolved. Everything else is derived, so re-running is a
no-op once the database agrees with the file.

Dry run by default:

    .venv/bin/python backfill_nodelist_identity.py
    .venv/bin/python backfill_nodelist_identity.py --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.core.config import init_config
from backend.core.database import get_session, init_database
from backend.models.database import Client, League, LeagueMembership
from backend.services.nodes_parser import NodesFileParser


def _find_nodes_file(game_folder: str, game_type: str) -> Path | None:
    """The games are case-inconsistent about this filename; so are backups."""
    folder = Path(game_folder)
    if not folder.is_dir():
        return None
    wanted = "brnodes.dat" if game_type == "bre" else "fenodes.dat"
    for entry in folder.iterdir():
        if entry.name.lower() == wanted and entry.is_file():
            return entry
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    args = parser.parse_args()

    config = init_config(args.config)
    init_database(f"sqlite:///{config.database.path}")
    db = get_session()

    hub_index_raw = config._raw.get("hub", {}).get("bbs_index", "01")
    try:
        hub_index = int(str(hub_index_raw), 10)
    except (TypeError, ValueError):
        print(f"[hub] bbs_index is {hub_index_raw!r}, which is not a number", file=sys.stderr)
        return 2

    hub_config = config._raw.get("hub", {})
    dosemu = config._raw.get("dosemu", {})
    changes = 0
    warnings = 0
    hub_location_fixes: dict[str, str] = {}

    for league_number, games in sorted(dosemu.items()):
        if not isinstance(games, dict):
            continue  # [dosemu] scalars like term/timeout live at the top level
        for game_type, settings in sorted(games.items()):
            if not isinstance(settings, dict):
                continue
            game_folder = settings.get("game_folder")
            if not game_folder:
                continue

            nodes_file = _find_nodes_file(game_folder, game_type)
            if not nodes_file:
                print(f"{league_number}.{game_type}: no nodes.dat in {game_folder}; skipped")
                warnings += 1
                continue

            file_parser = NodesFileParser(nodes_file)
            if not file_parser.parse():
                for error in file_parser.errors:
                    print(f"{league_number}.{game_type}: {nodes_file.name}: {error}")
                warnings += 1
                continue

            league = (
                db.query(League)
                .filter(
                    League.league_id == league_number,
                    League.game_type == ("B" if game_type == "bre" else "F"),
                )
                .first()
            )
            if not league:
                print(f"{league_number}.{game_type}: no such league in the database; skipped")
                warnings += 1
                continue

            nodes = {node.bbs_index: node for node in file_parser.nodes}
            label = f"{league.full_id}"

            # The hub's own address, and with it the HOST line.
            hub_node = nodes.get(hub_index)
            if not hub_node:
                print(
                    f"{label}: {nodes_file.name} has no entry for the hub's index "
                    f"{hub_index}; hub_fidonet_address left unset and nodelist "
                    f"generation stays blocked"
                )
                warnings += 1
            else:
                if (league.hub_fidonet_address or "") != hub_node.fidonet_address:
                    print(
                        f"{label}: hub_fidonet_address "
                        f"{league.hub_fidonet_address!r} -> {hub_node.fidonet_address!r}"
                    )
                    league.hub_fidonet_address = hub_node.fidonet_address
                    changes += 1

                # Preserve whatever routing form the league already uses.
                routes_mail = bool(hub_node.routing_targets)
                if (league.hub_routes_mail is not False) != routes_mail:
                    print(
                        f"{label}: hub_routes_mail {league.hub_routes_mail!r} -> "
                        f"{routes_mail!r} ({nodes_file.name} line 1 is "
                        f"{'a HOST directive' if routes_mail else 'a bare index'})"
                    )
                    league.hub_routes_mail = routes_mail
                    changes += 1

                # The hub's location lines come from [hub] config, which this
                # script has no business rewriting. Report the mismatch so the
                # operator can copy the value across by hand.
                for field, value in (
                    ("city", hub_node.city),
                    ("state", hub_node.state),
                    ("country", hub_node.country),
                ):
                    configured = hub_config.get(field, "") or ""
                    if configured.strip() != (value or "").strip():
                        hub_location_fixes[field] = value
                        warnings += 1

            # Each member's location lines.
            memberships = (
                db.query(LeagueMembership, Client)
                .join(Client, LeagueMembership.client_id == Client.id)
                .filter(
                    LeagueMembership.league_id == league.id,
                    LeagueMembership.is_active == True,
                    LeagueMembership.bbs_index.isnot(None),
                )
                .all()
            )
            for membership, client in memberships:
                node = nodes.get(membership.bbs_index)
                if not node:
                    print(
                        f"{label}: index {membership.bbs_index} ({client.bbs_name}) "
                        f"is not in {nodes_file.name}"
                    )
                    warnings += 1
                    continue

                if node.bbs_name.strip().lower() != client.bbs_name.strip().lower():
                    print(
                        f"{label}: index {membership.bbs_index} name disagrees - "
                        f"database {client.bbs_name!r}, {nodes_file.name} "
                        f"{node.bbs_name!r} (not changed; decide which is right)"
                    )
                    warnings += 1

                for field, value in (
                    ("city", node.city),
                    ("state", node.state),
                    ("country", node.country),
                ):
                    if (getattr(client, field) or "") != value:
                        print(
                            f"{label}: {client.bbs_name} {field} "
                            f"{getattr(client, field)!r} -> {value!r}"
                        )
                        setattr(client, field, value or None)
                        changes += 1

    if hub_location_fixes:
        print(
            "\nThe hub's own city/state/country come from [hub] config, not the "
            "database, so they are not written here. To match the nodes.dat the "
            "games use, set in config.toml under [hub]:"
        )
        for field, value in hub_location_fixes.items():
            print(f'    {field} = "{value}"')

    if args.apply:
        db.commit()
        print(f"\napplied {changes} change(s), {warnings} warning(s)")
    else:
        db.rollback()
        print(f"\ndry run: {changes} change(s) would be made, {warnings} warning(s)")
        print("re-run with --apply to write them")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
