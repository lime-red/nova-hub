"""Immortal Barons traffic, followed through the hub the way BRE's is.

The packets are built here to IB's own structs (internal/game/ibbs*.go), as
encoding/json writes them; tests/fixtures/ib_packet_shape.txt is IB's frozen
copy of that shape, and the first test checks every field the hub reads is in
it. The dev league has no war traffic yet, so these are not captures.

League 909I: the hub's game is board 1, the Coordinator; boards 2 and 3 play.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from backend.models.database import (
    AttackSighting, GameReset, League, LeagueGameSettings, Packet, TrafficSighting,
)
from backend.services import attack_trace, ib_packet, traffic_trace
from tests.test_attack_trace import api, db  # noqa: F401

SHAPE = Path(__file__).parent / "fixtures" / "ib_packet_shape.txt"
HUB = "01"
_seq = iter(range(1, 10_000))


@pytest.fixture
def league(db):
    lg = League(league_id="909", game_type="I", name="IB 909I")
    db.add(lg)
    db.commit()
    return lg


def t(day, hhmm="12:00"):
    h, m = hhmm.split(":")
    return datetime(2026, 10, day, int(h), int(m))


def packet(src, dst, date="2026-10-01", **fields):
    """A packet as IB marshals one: every non-omitempty field present."""
    p = {"FromBoard": f"Board {src}", "ToBoard": f"Board {dst}", "Date": date,
         "Scores": None, "Attacks": None, "Terrors": None, "Results": None,
         "LeagueConfig": None, "LeagueNodes": None, "Recon": None, "ReconReports": None,
         "Annihilator": None, "TimeChecks": None, "IPMessages": None, "Seq": 1,
         "Signature": None, "BoardSig": "c2lnbmVk", "Reset": None, "League": 909,
         "Hops": 0, "Protocol": 5, "FromNode": src, "ToNode": dst}
    p.update(fields)
    return p


def store(db, league, p, at, taken=None):
    src, dst = p["FromNode"], p["ToNode"]
    content = json.dumps(p).encode()
    row = Packet(filename=f"L909-{src}-{next(_seq):012d}-{dst}.BRP", league_id=league.id,
                 source_bbs_index=f"{src:02X}", dest_bbs_index=f"{dst:02X}",
                 sequence_number=1, file_size=len(content), file_data=content,
                 uploaded_at=at)
    if f"{src:02X}" == HUB:
        row.processed_at = at
    if taken:
        if f"{dst:02X}" == HUB:
            row.processed_at = taken
        else:
            row.downloaded_at, row.is_downloaded = taken, True
    db.add(row)
    db.commit()
    attack_trace.record(db, row, content)
    return row


def contributors(troopers, jets=0, tanks=0, bombers=0, owner="h1"):
    return [{"Owner": owner, "AttackForce": {"Troopers": troopers, "Jets": jets,
                                             "Tanks": tanks, "Bombers": bombers}}]


def attack(ident, frm="Moggill", target="Brisbane", kind=1, group=False):
    a = {"ID": ident, "FromBoard": "Board 2", "TargetEmpire": target, "Offense": 900,
         "Contributors": contributors(100, 20, 5), "Kind": kind, "Group": group}
    if frm:
        a["FromEmpire"] = frm
    return a


def result(ident, target="Brisbane", won=True, kind="Normal Attack", land=12):
    return {"ID": ident, "TargetBoard": "Board 3", "TargetEmpire": target, "LandTaken": land,
            "Won": won, "Survivors": contributors(90, 18, 5), "Kind": kind,
            "Outcome": "success" if won else "failure"}


def config(**kw):
    c = {"GameStartDate": "", "JoinDate": "", "TurnsPerDay": 10, "ProtectionTurns": 20,
         "GameLength": 0, "IdleDaysRemove": 15, "InitialMarketLand": 1000, "LandPerDay": 50,
         "MoneyCapBillions": 2, "InterestRate": 5, "StdInvestRate": 10, "SteadyInvest": False,
         "FoodUnlimited": False, "MaxTaxRate": 30, "PlanetaryTaxRate": 5, "MaxRegions": 20,
         "MaxIndividualAttacks": 5, "MaxGroupAttacks": 2, "MaxTerrorOps": 10,
         "MaxBombingOps": 3, "LostForcesDays": 3, "BombingOps": True, "MissileOps": True,
         "ClingyAnnihilator": True, "LocalAttacks": False, "LocalAttackScoring": False,
         "DupeChecking": True, "MinBoardVersion": "", "IPTrading": True, "Pirates": False,
         "MaxPlayers": 25, "BuyMilitary": 2, "MaintCosts": 0, "TradeCosts": 2,
         "RegionCosts": 1, "AttackCosts": 0, "TerrorCosts": 3, "AttackDamage": 0,
         "AttackRewards": 2, "SlappenheimerHandling": 3, "SabreConstantDial": 7}
    c.update(kw)
    return c


def journeys(db, **kw):
    return {j.attack_id: j for j in attack_trace.journeys(db, HUB, **kw)}


def traffic(db):
    return traffic_trace.journeys(traffic_trace.events(db, HUB, since=t(1, "00:00")))


# ── the wire shape ────────────────────────────────────────────────────────
def frozen_shape():
    shape, current = {}, None
    for line in SHAPE.read_text().splitlines():
        if not line.startswith(" "):
            current = shape.setdefault(line.rstrip(":"), set())
            continue
        name = line.split()[0]
        tag = line.split('json:"')[1].split(",")[0].split('"')[0] if 'json:"' in line else ""
        current.add(tag or name)
    return shape


READ = {
    "Packet": {"Date", "Attacks", "Terrors", "SpecialOps", "Results", "LeagueConfig",
               "Recon", "ReconReports", "Annihilator", "IPMessages", "SpyGuys",
               "TradeDeals", "TradeReceipts", "Reset"},
    "RemoteAttack": {"ID", "TargetEmpire", "Contributors", "Kind", "Group", "FromEmpire"},
    "Contribution": {"AttackForce"},
    "AttackForce": {"Troopers", "Jets", "Tanks", "Bombers"},
    "RemoteTerror": {"ID", "TargetEmpire", "Agents", "Op", "FromEmpire"},
    "RemoteSpecialOp": {"ID", "FromEmpire", "TargetEmpire", "Op", "Dial"},
    "AttackResult": {"ID", "TargetEmpire", "LandTaken", "Won", "Survivors", "Kind",
                     "Outcome", "Backfired"},
    "ReconRequest": {"TargetEmpire"},
    "SpyReport": {"Empire", "Date", "Land", "Troopers", "Jets", "Turrets", "Tanks", "Morale"},
    "AnnihilatorStatus": {"Funded", "Launched", "ArrivesAt", "Intact", "Dismantled"},
    "IPMessage": {"FromBoard", "FromEmpire", "ToCoordinator", "ToEmpire", "ToEmpires", "When"},
    "SpyGuyDispatch": {"Days"},
    "IPTradeDeal": {"ID", "FromEmpire", "ToEmpire", "Goods"},
    "IPTradeReceipt": {"ID", "FromEmpire", "ToEmpire", "Goods", "Delivered"},
    "LeagueReset": {"OnDate"},
    "LeagueConfig": set(ib_packet._NUMBERS) | set(ib_packet._LEVELS) | set(ib_packet._TOGGLES)
    | set(ib_packet._OWN_NUMBERS) | set(ib_packet._OWN_TOGGLES)
    | {"GameStartDate", "JoinDate", "BuyMilitary", "MinBoardVersion",
       "SlappenheimerHandling", "SabreConstantDial"},
}


def test_every_field_the_hub_reads_is_in_ibs_frozen_shape():
    shape = frozen_shape()
    for struct, fields in READ.items():
        assert fields <= shape[struct], (struct, fields - shape[struct])


def test_a_packet_that_is_not_json_is_refused():
    with pytest.raises(ib_packet.PacketFormatError):
        ib_packet.parse(b"\x00not json")
    with pytest.raises(ib_packet.PacketFormatError):
        ib_packet.parse(b"[1, 2]")


# ── attacks ───────────────────────────────────────────────────────────────
def a_war(db, league):
    """Board 2's Moggill attacks Brisbane on board 3; the result comes home."""
    store(db, league, packet(2, 3, Attacks=[attack(7)]), t(1, "10:00"), t(1, "11:00"))
    store(db, league, packet(3, 2, Results=[result(7)]), t(1, "11:05"), t(1, "12:00"))


def test_an_attack_joins_its_result_by_board_and_id(db, league):
    a_war(db, league)
    j, = journeys(db).values()
    assert j.attack_id == "0.2.7"
    assert (j.from_planet, j.attacker_name, j.to_planet, j.target_name) == \
        (2, "Moggill", 3, "Brisbane")
    assert (j.attacker, j.target) == (None, None)          # IB realms have no letters
    assert j.attack_type == "Normal Attack" and j.stage == "result delivered"
    assert j.launched == datetime(2026, 10, 1)


def test_the_same_number_from_another_board_is_another_attack(db, league):
    a_war(db, league)
    store(db, league, packet(3, 2, Attacks=[attack(7, "Brisbane", "Moggill")]),
          t(1, "13:00"))
    assert set(journeys(db)) == {"0.2.7", "0.3.7"}


def test_sightings_hold_no_forces(db, league):
    a_war(db, league)
    assert db.query(AttackSighting).count() == 2
    assert not {"offense", "troopers", "contributors"} & set(AttackSighting.__table__.columns.keys())


def test_a_group_attack_is_named_as_one(db, league):
    store(db, league, packet(2, 3, Attacks=[attack(8, frm=None, group=True)]), t(1))
    j, = journeys(db).values()
    assert j.attack_type == "Group Attack" and j.attacker_name is None


def test_ops_results_are_not_attacks(db, league):
    store(db, league, packet(3, 2, Results=[result(9, kind="terror"),
                                            result(10, kind="nuclear")]), t(1))
    assert db.query(AttackSighting).count() == 0
    assert {s.kind for s in db.query(TrafficSighting)} == {"Terrorist Result", "Special Result"}


# ── lost forces ───────────────────────────────────────────────────────────
def settings_from_hub(db, league, **kw):
    store(db, league, packet(1, 0, LeagueConfig=config(**kw)), t(1, "01:00"))


def test_lost_forces_days_come_from_the_coordinators_config(db, league):
    settings_from_hub(db, league, LostForcesDays=2)
    row = db.get(LeagueGameSettings, league.id)
    assert row.lost_attack_days == 2 and row.protection_turns == 20
    assert row.settings["trade_costs"] == "H" and row.settings["region_costs"] == "L"
    assert row.settings["terrorist_costs"] == "N" and row.settings["buy_military"] == "L"
    assert (row.settings["sabre"], row.settings["sabre_dial"]) == ("C", 7)
    assert row.settings["gooie"] is True and row.settings["ip_trading"] is True
    assert "MoneyCapBillions" not in row.settings and row.settings["idle_days_remove"] == 15


def test_ib_gives_up_a_whole_lost_forces_span_after_the_launch_day(db, league, monkeypatch):
    settings_from_hub(db, league, LostForcesDays=2)
    store(db, league, packet(2, 3, Attacks=[attack(7)]), t(1, "10:00"), t(1, "11:00"))
    monkeypatch.setattr(attack_trace, "_now", lambda: t(3, "23:00"))
    j, = journeys(db).values()
    # Launched on the 1st: the 2nd and 3rd pass, the 4th's maintenance gives up.
    assert j.mit_due_local == datetime(2026, 10, 4) and j.mit is None
    monkeypatch.setattr(attack_trace, "_now", lambda: t(4, "01:00"))
    assert next(iter(journeys(db).values())).mit == "overdue"
    # A result handed over after that midnight: IB packets are not timed.
    store(db, league, packet(3, 2, Results=[result(7)]), t(4, "02:00"), t(4, "03:00"))
    assert next(iter(journeys(db).values())).mit == "possible"


def test_lost_forces_days_of_zero_is_never(db, league, monkeypatch):
    settings_from_hub(db, league, LostForcesDays=0)
    store(db, league, packet(2, 3, Attacks=[attack(7)]), t(1, "10:00"))
    monkeypatch.setattr(attack_trace, "_now", lambda: t(30))
    j, = journeys(db).values()
    assert j.mit_due is None and j.mit is None


def test_a_member_cannot_send_league_orders(db, league):
    store(db, league, packet(2, 0, LeagueConfig=config(LostForcesDays=30),
                             Reset={"OnDate": "2026-11-01", "Announced": "x"}), t(1))
    assert db.get(LeagueGameSettings, league.id) is None
    assert db.query(GameReset).count() == 0


# ── resets ────────────────────────────────────────────────────────────────
def test_a_reset_is_in_the_history_and_starts_a_new_count(db, league):
    a_war(db, league)
    store(db, league, packet(1, 0, LeagueConfig=config(GameStartDate="2026-10-05"),
                             Reset={"OnDate": "2026-10-05", "Announced": "New game"}),
          t(2, "01:00"))
    reset, = db.query(GameReset).all()
    assert reset.source == "observed" and reset.game_start == datetime(2026, 10, 5)
    assert reset.settings["lost_attack_days"] == 3
    # Board 2's counter starts again: its new attack 7 is not the old one.
    store(db, league, packet(2, 3, Attacks=[attack(7)], date="2026-10-05"), t(5, "10:00"))
    assert set(journeys(db)) == {"0.2.7", "261005.2.7"}


# ── the rest of the traffic ───────────────────────────────────────────────
def kinds(js):
    return sorted((j.kind, j.stage, len(j.events)) for j in js)


def test_a_send_spy_brings_its_result_and_report_home_as_one_journey(db, league):
    spy = {"ID": 11, "FromBoard": "Board 2", "TargetEmpire": "Brisbane", "Agents": 4,
           "Op": 1, "FromEmpire": "Moggill"}
    store(db, league, packet(2, 3, Terrors=[spy]), t(1, "10:00"), t(1, "11:00"))
    report = {"Board": "Board 3", "Empire": "Brisbane", "Date": "2026-10-01", "Land": 400,
              "Offense": 1, "Defense": 2, "Gold": 3, "Troopers": 50, "Jets": 0,
              "Turrets": 0, "Tanks": 0, "Morale": 90}
    store(db, league, packet(3, 2, Results=[result(11, kind="terror")], ReconReports=[report]),
          t(1, "11:05"), t(1, "12:00"))
    j, = traffic(db)
    assert kinds([j]) == [("Terrorist Op", "result delivered", 3)]
    assert (j.route.from_name, j.route.to_name) == ("Moggill", "Brisbane")


def test_a_league_wide_recon_is_answered_by_one_report_per_realm(db, league):
    store(db, league, packet(2, 3, Recon=[{"ID": 12, "FromBoard": "Board 2",
                                           "FromOwner": "h1", "TargetEmpire": ""}]),
          t(1, "10:00"), t(1, "11:00"))
    reports = [{"Board": "Board 3", "Empire": n, "Date": "2026-10-01"} for n in ("A1", "B2")]
    store(db, league, packet(3, 2, ReconReports=reports), t(1, "11:05"), t(1, "12:00"))
    assert kinds(traffic(db)) == [("Recon Request", "result delivered", 3)]


def test_trades_and_special_ops_pair_by_id(db, league):
    deal = {"ID": 13, "FromBoard": "Board 2", "FromEmpire": "Moggill", "ToEmpire": "Brisbane",
            "Goods": {"Food": 100, "Gold": 0}, "When": "x"}
    op = {"ID": 14, "FromBoard": "Board 2", "FromEmpire": "Moggill", "TargetEmpire": "Brisbane",
          "Op": "nuclear"}
    store(db, league, packet(2, 3, TradeDeals=[deal], SpecialOps=[op]), t(1, "10:00"),
          t(1, "11:00"))
    receipt = {"ID": 13, "FromEmpire": "Moggill", "ToEmpire": "Brisbane",
               "Goods": {"Food": 100}, "Delivered": True}
    store(db, league, packet(3, 2, TradeReceipts=[receipt],
                             Results=[result(14, kind="nuclear", won=False)]),
          t(1, "11:05"))
    assert kinds(traffic(db)) == [("Special Op", "result awaiting pickup", 2),
                                  ("Trade Deal", "result awaiting pickup", 2)]


def test_a_gooie_is_followed_by_its_status(db, league):
    status = {"FromBoard": "Board 2", "Funded": True, "Launched": False, "ArrivesDay": 0,
              "Intact": 0, "Dismantled": False}
    store(db, league, packet(2, 3, Annihilator=status), t(1))
    store(db, league, packet(2, 3, Annihilator={**status, "Launched": True,
                                                "ArrivesAt": "2026-10-04T00:00:00Z"}), t(2))
    store(db, league, packet(2, 3, Annihilator={**status, "Dismantled": True}), t(3))
    j, = traffic(db)
    assert j.kind == "Gooie Kablooie" and j.stage == "destroyed"


def test_a_message_names_who_it_is_for_and_never_holds_its_body(db, league):
    m = {"FromBoard": "Board 2", "FromEmpire": "Moggill", "ToCoordinator": False,
         "ToEmpire": "Brisbane", "ToEmpires": ["Brisbane"], "When": "x", "Body": "secret plans"}
    store(db, league, packet(2, 3, IPMessages=[m]), t(1))
    s, = db.query(TrafficSighting).all()
    assert (s.kind, s.from_name, s.to_name) == ("Message", "Moggill", "Brisbane")
    assert "secret" not in json.dumps([getattr(s, c) for c in TrafficSighting.__table__.columns.keys()
                                       if c != "packet_id"], default=str)


# ── the reveals ───────────────────────────────────────────────────────────
def test_an_admin_reveals_an_ib_attacks_forces(api, db, league):
    a_war(db, league)
    got = api.get("/management/api/v1/attacks/0.2.7/forces").json()
    assert got["sent"] == {"troopers": 100, "jets": 20, "tanks": 5, "bombers": 0}
    assert got["returned"] == {"troopers": 90, "jets": 18, "tanks": 5, "bombers": 0}
    assert got["lost"] == {"troopers": 10, "jets": 2, "tanks": 0, "bombers": 0}
    assert (got["success"], got["regions_captured"], got["outcome"]) == (True, 12, "success")


def test_the_listing_finds_an_ib_attack_by_id(api, db, league):
    a_war(db, league)
    j, = api.get("/management/api/v1/attacks/", params={"attack_id": "0.2.7"}).json()
    assert (j["attacker"], j["attacker_name"], j["target_name"]) == (None, "Moggill", "Brisbane")


def test_an_admin_reveals_an_ib_op_but_never_a_message(api, db, league):
    spy = {"ID": 11, "FromBoard": "Board 2", "TargetEmpire": "Brisbane", "Agents": 4,
           "Op": 3, "FromEmpire": "Moggill"}
    m = {"FromBoard": "Board 2", "FromEmpire": "Moggill", "ToCoordinator": False,
         "ToEmpire": "Brisbane", "When": "x", "Body": "secret plans"}
    store(db, league, packet(2, 3, Terrors=[spy], IPMessages=[m]), t(1))
    keys = {s.kind: s.event_key for s in db.query(TrafficSighting)}
    got = api.get(f"/management/api/v1/traffic/{keys['Terrorist Op']}/details").json()
    assert got["details"] == {"operation": "Demoralize", "sent": 4}
    assert api.get(f"/management/api/v1/traffic/{keys['Message']}/details").status_code == 403
