"""Sightings name their realms as the hub knew them when it read the packet.

The rig's 901B packets each carry their planet's Recon Update: planet 2's realm
A is "Raider Two" (player 1032), the hub's planet 1 has "Hub Target" (1235) and
later "Second Target" (1236). A packet read before the hub had heard of a realm
has no name for it, and a name once recorded is not changed by a later reset or
a realm taking the slot over.
"""
import struct

from backend.models.database import AttackSighting, RealmName, TrafficSighting
from backend.services import bre_packet, realm_names
from tests.test_attack_trace import (  # noqa: F401
    api, by_id, db, league, raw, rig_session, store, t,
)
from tests.test_traffic import ROUNDS, journeys, store_built, three_days


def recon_update(planet, realms):
    """A Recon Update announcing `realms` ({letter: (player id, name)})."""
    d = bytearray(bre_packet.RECON_UPDATE_SIZE)
    d[0] = planet
    for letter, (player, name) in realms.items():
        k = ord(letter) - ord("A")
        o = 2 + 31 * k
        d[o] = len(name)
        d[o + 1:o + 1 + len(name)] = name.encode()
        struct.pack_into("<i", d, 1077 + 4 * k, player)
    return (bre_packet.RECON_UPDATE, planet, 1, bytes(d))


def test_a_recon_update_lists_its_planets_realms():
    rec = next(r for r in bre_packet.parse(raw("forces_results_901b0102.010"))
               if r.type == bre_packet.RECON_UPDATE)
    assert [(r.letter, r.player_id, r.name) for r in bre_packet.realms(rec)] == \
        [("A", 1235, "Hub Target"), ("B", 1236, "Second Target")]


def test_attacks_are_named_from_what_the_hub_had_heard(db, league):
    rig_session(db, league)
    first = db.query(AttackSighting).filter(AttackSighting.is_result.is_(False)).first()
    # The attack packet carries its own planet's realms, read before its attacks;
    # the hub's planet had not announced its realms yet.
    assert (first.attacker_name, first.target_name) == ("Raider Two", None)
    result = db.query(AttackSighting).filter(AttackSighting.is_result.is_(True)).first()
    assert (result.attacker_name, result.target_name) == ("Raider Two", "Hub Target")
    js = by_id(db)
    assert (js["4f7338aad40f3f2e"].attacker_name, js["4f7338aad40f3f2e"].target_name) == \
        ("Raider Two", "Hub Target")
    assert js["ca9564ee5fb676f8"].target_name == "Second Target"     # sent at 1B


def test_a_name_is_kept_after_the_slot_changes_hands(db, league):
    rig_session(db, league)
    before = {s.id: s.attacker_name for s in db.query(AttackSighting)}
    # A reset: planet 2's realm A is someone else now.
    store_built(db, league, "901B0201.050",
                [recon_update(2, {"A": (2001, "Usurper")})],
                t("09:00", day=3, month=10), t("09:01", day=3, month=10))
    assert {s.id: s.attacker_name for s in db.query(AttackSighting)} == before
    assert realm_names.Names(db, league.id).name(2, "A") == "Usurper"


def test_traffic_is_named_by_player_id(db, league):
    three_days(db, league)
    names = {(s.kind, s.packet.filename): (s.from_name, s.to_name)
             for s in db.query(TrafficSighting)}
    # Day one: the hub's realms were not yet announced when node 2's op came in.
    assert names[("Terrorist Op", "901B0201.015")] == ("Raider Two", None)
    # Day two, after the hub's results packet listed them.
    assert names[("Trade Deal", "901B0201.017")] == ("Raider Two", "Hub Target")
    assert names[("Terrorist Op", "901B0201.019")] == ("Raider Two", "Second Target")
    # The journey reads the same way round as its letters.
    op = next(j for j in journeys(db) if j.kind == "Terrorist Op" and j.route.to_letter == "B")
    assert (op.route.from_name, op.route.to_name) == ("Raider Two", "Second Target")


def test_message_recipients_are_named_when_known(db, league):
    out_fix, out_name, *_ = ROUNDS[0]
    # The hub's realms are known before the messages arrive this time.
    store_built(db, league, "901B0102.040",
                [recon_update(1, {"A": (1235, "Hub Target"), "B": (1236, "Second Target")})],
                t("10:00", day=1, month=10), t("10:01", day=1, month=10))
    store(db, league, out_fix, out_name, at=t("11:00", day=1, month=10))
    got = sorted((s.recipients, s.recipient_names) for s in db.query(TrafficSighting)
                 if s.kind == "Message")
    assert got == [("-999", None), ("1235", ["Hub Target"]),
                   ("1235,1236", ["Hub Target", "Second Target"])]


def test_an_unknown_player_id_is_not_named_after_the_letters_old_holder(db, league):
    names = realm_names.Names
    realm_names.remember(db, league.id, 1, [bre_packet.Realm("A", 1235, "Hub Target")],
                         None, t("10:00"))
    n = names(db, league.id)
    assert n.name(1, "A", 1235) == "Hub Target"
    assert n.name(1, "A", 9999) is None         # a newer realm in slot A
    assert n.name(1, "A") == "Hub Target"       # attacks carry the letter alone


def test_an_older_list_changes_nothing_and_an_empty_slot_is_forgotten(db, league):
    realm_names.remember(db, league.id, 1, [bre_packet.Realm("A", 1, "Old"),
                                            bre_packet.Realm("B", 2, "Gone soon")],
                         None, t("10:00"))
    realm_names.remember(db, league.id, 1, [bre_packet.Realm("A", 3, "New")], None, t("12:00"))
    realm_names.remember(db, league.id, 1, [bre_packet.Realm("A", 1, "Old")], None, t("11:00"))
    assert {(r.letter, r.name) for r in db.query(RealmName)} == {("A", "New")}


def test_a_member_cannot_name_another_planets_realms(db, league):
    # Node 2 uploads a Recon Update of its own that says it is planet 3's.
    rtype, _, _, data = recon_update(3, {"A": (5, "Forged")})
    store_built(db, league, "901B0201.060", [(rtype, 2, 1, data)],
                t("10:00", day=1, month=10), t("10:01", day=1, month=10))
    assert db.query(RealmName).count() == 0


def test_the_listing_carries_the_names(api, db, league):
    rig_session(db, league)
    j, = api.get("/management/api/v1/attacks/",
                 params={"attack_id": "4f7338aad40f3f2e"}).json()
    assert (j["attacker"], j["attacker_name"], j["target"], j["target_name"]) == \
        ("A", "Raider Two", "A", "Hub Target")

