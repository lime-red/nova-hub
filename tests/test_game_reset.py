"""Drafting a game reset: the settings catalogue, its checks, and the draft API.

The catalogue is what the driver will type into the game's editor, so these
tests pin it to what the editors showed on the rig (14 Oct 2026): every field,
in order, with its default and maximum.
"""
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, service_app, management_app
from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.database import AuditEvent, Base, GameReset, League, SysopUser
from backend.services.game_reset import drafts
from backend.services import ib_packet
from backend.services.game_reset.fields import CHOICE, DATE, NUMBER, defaults, fields_for

TODAY = date(2026, 10, 15)

# (label, default, max) in editor order, as read off the rig.
BRE_EDITOR = [
    ("Game Start Date", None, None), ("Game Join Date", "2999-01-01", None),
    ("Turns Per Day", 8, 20), ("Turns of Protection", 20, 200),
    ("Initial Market Land", 0, 50000), ("Land Created / Day", 1000, 5000),
    ("Interest Rate", 50, 200), ("Standard Investment Rate", 35, 100),
    ("Steady Investment Rate", False, None), ("Tax Rate", 50, 200),
    ("Max Purchasable Regions", 500, 10000), ("Max Players Per BBS", 25, 25),
    ("Buy Military", "Y", None), ("Maintenance Costs", "M", None),
    ("Trade Deal Costs", "M", None), ("Region Costs", "M", None),
    ("Attack Damage", "M", None), ("Attack Rewards", "M", None),
    ("Sabre Handling", "U", None), ("Sabre Constant Setting", 5, 10),
    ("Attack Costs", "M", None), ("Terrorist Costs", "M", None),
    ("Individual Attacks / Day", 1, 10), ("Group Attacks / Day", 1, 10),
    ("Terrorist Attacks / Day", 10, 10000), ("Bombings / Day", 5, 10000),
    ('Days for "Lost Attacks"', 7, 100), ("Gooie Kablooies", True, None),
    ("Bombing Operations", True, None), ("Missile Operations", True, None),
    ("Local Attacks", True, None), ("Local Attack Scoring", False, None),
    ("Dupe Checking", True, None),
]
FE_EDITOR = [
    ("Game Start Date", None, None), ("Game Join Date", "2999-01-01", None),
    ("Turns Per Day", 8, 15), ("Turns of Protection", 20, 50),
    ("Initial Market Land", 20000, 50000), ("Land Created / Day", 2000, 5000),
    ("Standard Wages", 15, 200), ("Starting Land", 100, 2000),
    ("Starting People", 100, 2000), ("Starting Silver", 15000, 100000),
    ("Starting Food", 2000, 25000), ("Starting Stone Blocks", 200, 500),
    ("Starting Weapons", 400, 2000), ("Starting Runes", 25, 1000),
    ("Max Attacks", 7, 12), ("Max Spells", 15, 40), ("Max Players", 25, 25),
    ('Days for "Lost Attacks"', 10, 30), ("Local Attacks", True, None),
    ("Local Attack Scoring", False, None), ("Dupe Checking", False, None),
]


@pytest.mark.parametrize("game,editor", [("B", BRE_EDITOR), ("F", FE_EDITOR)])
def test_catalogue_matches_the_editor(game, editor):
    got = [(f.label, f.default, f.max) for f in fields_for(game)]
    assert got == editor


@pytest.mark.parametrize("game", ["B", "F"])
def test_every_field_is_complete(game):
    fields = fields_for(game)
    assert len({f.id for f in fields}) == len(fields)
    for f in fields:
        if f.id != "sabre_dial":  # the editor shows Sabre Handling's topic
            assert f.help_title and f.help, f.id
            assert "toggled" not in f.help and "^\\" not in f.help, f.id
        if f.kind == NUMBER:
            assert f.min == 0 and f.default <= f.max, f.id
            assert f.advised_min is None or f.advised_reason, f.id
        if f.kind == CHOICE:
            assert f.default in [letter for letter, _ in f.choices], f.id
    # The defaults pass our own checks, without a warning.
    settings, errors = drafts.validate(game, {}, TODAY)
    assert errors == [] and drafts.warnings(game, settings) == []


# IB's DefaultConfig, as a fresh Coordinator's config.json holds it (dev 902I,
# v0.2.6), but MoneyCapBillions, which IB fixed at 2 (#205).
IB_DEFAULTS = {
    "GameStartDate": "", "JoinDate": "", "TurnsPerDay": 10, "ProtectionTurns": 15,
    "GameLength": 0, "IdleDaysRemove": 10, "InitialMarketLand": 0, "LandPerDay": 1000,
    "InterestRate": 50, "StdInvestRate": 35, "SteadyInvest": False,
    "FoodUnlimited": False, "MaxTaxRate": 50, "PlanetaryTaxRate": 5, "MaxRegions": 500,
    "MaxLocalAttacks": 0, "MaxIndividualAttacks": 1, "MaxGroupAttacks": 1,
    "MaxTerrorOps": 10, "MaxBombingOps": 5, "LostForcesDays": 3, "LocalAttacks": True,
    "LocalAttackScoring": False, "DupeChecking": True, "MinBoardVersion": "",
    "IPTrading": True, "Pirates": True, "BombingOps": True, "MissileOps": True,
    "ClingyAnnihilator": True, "MaxPlayers": 25, "BuyMilitary": 0, "MaintCosts": 0,
    "TradeCosts": 0, "RegionCosts": 0, "AttackCosts": 0, "TerrorCosts": 0,
    "AttackDamage": 0, "AttackRewards": 0, "SabreHandling": 1, "SabreConstantDial": 5,
}


def ib_league_config_fields():
    """LeagueConfig's fields in IB's frozen packet shape, by config.json's names."""
    shape = Path(__file__).parent / "fixtures" / "ib_packet_shape.txt"
    lines = shape.read_text().split("\nLeagueConfig:\n", 1)[1].splitlines()
    out = set()
    for line in lines:
        if not line.startswith(" "):
            break
        tag = line.split('json:"')[1].split(",")[0].split('"')[0] if 'json:"' in line else ""
        out.add(tag or line.split()[0])
    return out - {"MoneyCapBillions", "SlappenheimerHandling"} | {"SabreHandling"}


def test_the_ib_form_sets_every_league_rule_at_ibs_defaults():
    assert ib_packet.config_json(defaults("I")) == IB_DEFAULTS
    assert set(IB_DEFAULTS) == ib_league_config_fields()


def test_ib_settings_survive_the_round_trip_through_a_packet():
    s, errors = drafts.validate("I", {
        "turns_per_day": 7, "join_date": "2026-11-20", "start_date": "2026-10-20",
        "attack_damage": "H", "region_costs": "N", "buy_military": "L", "sabre": "C",
        "sabre_dial": 9, "pirates": False, "game_length": 90, "planetary_tax_rate": 0,
        "local_attacks_per_day": 4, "min_board_version": "v0.2.6"}, TODAY)
    assert errors == [] and s["min_board_version"] == "0.2.6"
    sent = ib_packet.config_json(s)
    sent["SlappenheimerHandling"] = sent.pop("SabreHandling")
    back = ib_packet.league_settings(sent)
    assert back.game_started_at.date().isoformat() == "2026-10-20"
    assert {k: v for k, v in back.settings.items()} == {
        k: v for k, v in s.items() if k != "start_date"}


def test_ib_has_minimums_and_blank_dates():
    assert "turns_per_day" in errors_for("I", {"turns_per_day": 0})
    assert "interest_rate" in errors_for("I", {"interest_rate": 49})
    assert "investment_rate" in errors_for("I", {"investment_rate": 34})
    assert "tax_rate" in errors_for("I", {"tax_rate": 51})
    assert "indiv_attacks" in errors_for("I", {"indiv_attacks": 101})
    assert "attack_rewards" in errors_for("I", {"attack_rewards": "N"})
    settings, errors = drafts.validate("I", {}, TODAY)
    assert errors == [] and settings["join_date"] is None and settings["start_date"] is None
    assert settings["min_board_version"] == ""
    for bad in ("abc", "0.2.6-rc1", "1..2", 5):
        assert "min_board_version" in errors_for("I", {"min_board_version": bad}), bad
    assert errors_for("I", {"min_board_version": " V1.0 "}) == {}


def test_ib_fields_are_complete():
    fields = fields_for("I")
    assert len({f.id for f in fields}) == len(fields)
    for f in fields:
        assert f.help_title and f.help, f.id
        if f.kind == NUMBER:
            assert f.min <= f.default <= f.max, f.id
        if f.kind == CHOICE:
            assert f.default in [letter for letter, _ in f.choices], f.id
    settings, errors = drafts.validate("I", {}, TODAY)
    assert errors == [] and drafts.warnings("I", settings) == []


def test_choices_are_only_what_the_editor_accepts():
    bre = {f.id: f for f in fields_for("B")}
    assert [l for l, _ in bre["attack_damage"].choices] == ["H", "M", "L"]
    assert [l for l, _ in bre["attack_rewards"].choices] == ["H", "M", "L"]
    assert [l for l, _ in bre["region_costs"].choices] == ["H", "M", "L", "N"]
    assert [l for l, _ in bre["sabre"].choices] == ["N", "U", "R", "C"]


def errors_for(game, settings):
    return dict(drafts.validate(game, settings, TODAY)[1])


def test_numbers_reject_rather_than_clamp():
    assert "turns_per_day" in errors_for("B", {"turns_per_day": 21})
    assert "turns_per_day" in errors_for("F", {"turns_per_day": 16})
    assert "turns_per_day" not in errors_for("B", {"turns_per_day": 20})
    assert "turns_per_day" in errors_for("B", {"turns_per_day": -1})
    # 0 is the game's own minimum everywhere.
    assert errors_for("B", {"protection_turns": 0, "indiv_attacks": 0}) == {}
    assert "tax_rate" in errors_for("B", {"tax_rate": "50"})
    assert "tax_rate" in errors_for("B", {"tax_rate": True})


def test_below_the_usual_minimum_warns_but_is_allowed():
    # A free-for-all stage where nobody can buy any more land is a real choice.
    settings, errors = drafts.validate("B", {"max_region_purchase": 0,
                                             "turns_per_day": 0}, TODAY)
    assert errors == []
    warned = dict(drafts.warnings("B", settings))
    assert set(warned) == {"max_region_purchase", "turns_per_day"}
    assert "usual minimum of 1" in warned["max_region_purchase"]
    settings, errors = drafts.validate("F", {"max_attacks": 3}, TODAY)
    assert errors == [] and dict(drafts.warnings("F", settings)).keys() == {"max_attacks"}


def test_choices_toggles_and_unknowns():
    assert "attack_damage" in errors_for("B", {"attack_damage": "N"})
    assert "region_costs" not in errors_for("B", {"region_costs": "N"})
    assert "gooie" in errors_for("B", {"gooie": "yes"})
    assert "gooie" in errors_for("F", {"gooie": False})  # BRE only


def test_sabre_dial_only_with_constant():
    settings, errors = drafts.validate("B", {"sabre": "C", "sabre_dial": 9}, TODAY)
    assert errors == [] and settings["sabre_dial"] == 9
    assert "sabre_dial" in errors_for("B", {"sabre": "C", "sabre_dial": 11})
    settings, errors = drafts.validate("B", {"sabre": "R", "sabre_dial": 99}, TODAY)
    assert errors == [] and "sabre_dial" not in settings


def test_dates():
    later = (TODAY + timedelta(days=10)).isoformat()
    settings, errors = drafts.validate("B", {"start_date": later}, TODAY)
    assert errors == [] and settings["start_date"] == later
    assert drafts.validate("F", {"start_date": None}, TODAY)[0]["start_date"] is None
    assert "start_date" in errors_for("B", {"start_date": "2026-10-14"})
    assert "start_date" in errors_for("B", {"start_date": "15/10/2026"})
    assert "join_date" in errors_for("B", {"join_date": "3000-01-01"})
    assert "join_date" in errors_for("B", {"start_date": later,
                                           "join_date": TODAY.isoformat()})
    assert "join_date" in errors_for("B", {"join_date": None})


# --- the API ---------------------------------------------------------------

BASE = "/management/api/v1/leagues"


@pytest.fixture
def api():
    import backend.core.database as _db_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    saved_engine, saved_session = _db_mod.engine, _db_mod.SessionLocal
    _db_mod.engine, _db_mod.SessionLocal = engine, TestSession
    saved = {a: (a.dependency_overrides.get(get_db),
                 a.dependency_overrides.get(get_current_user))
             for a in (app, service_app, management_app)}
    for a in (app, service_app, management_app):
        a.dependency_overrides[get_db] = override_get_db
        a.dependency_overrides[get_current_user] = lambda: SysopUser(
            id=1, username="mantis", hashed_password="x", is_superuser=True)

    db = TestSession()
    league = League(league_id="015", game_type="B", name="015B", is_active=True)
    db.add(league)
    db.commit()
    yield TestClient(app, base_url="https://testserver"), db, league.id
    db.close()

    for a, (old_db, old_user) in saved.items():
        a.dependency_overrides.pop(get_db, None)
        a.dependency_overrides.pop(get_current_user, None)
        if old_db:
            a.dependency_overrides[get_db] = old_db
        if old_user:
            a.dependency_overrides[get_current_user] = old_user
    _db_mod.engine, _db_mod.SessionLocal = saved_engine, saved_session


def test_form_has_every_field_and_no_draft(api):
    client, _, lid = api
    r = client.get(f"{BASE}/{lid}/reset")
    assert r.status_code == 200
    body = r.json()
    assert [f["label"] for f in body["fields"]] == [e[0] for e in BRE_EDITOR]
    assert body["defaults"] == defaults("B")
    assert body["draft"] is None and body["full_id"] == "015B"
    tz = body["hub_timezone"]
    assert tz["name"] and re.fullmatch(r"[+-]\d\d:\d\d", tz["utc_offset"])
    assert tz["today"] == date.today().isoformat()


def test_save_update_conflict_and_discard(api):
    client, db, lid = api
    r = client.put(f"{BASE}/{lid}/reset/draft",
                   json={"settings": {"turns_per_day": 10}, "notes": "015 round 3"})
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["version"] == 1 and draft["settings"]["turns_per_day"] == 10
    assert draft["warnings"] == []
    assert draft["settings"]["tax_rate"] == 50  # filled from the defaults
    assert draft["updated_by"] == "mantis"

    r = client.put(f"{BASE}/{lid}/reset/draft",
                   json={"settings": {"turns_per_day": 12}, "version": 1})
    assert r.status_code == 200 and r.json()["draft"]["version"] == 2

    # A second editor still on version 1 must not overwrite version 2.
    r = client.put(f"{BASE}/{lid}/reset/draft",
                   json={"settings": {"turns_per_day": 6}, "version": 1})
    assert r.status_code == 409
    # Nor may a "new draft" replace the existing one.
    r = client.put(f"{BASE}/{lid}/reset/draft", json={"settings": {}})
    assert r.status_code == 409

    assert client.delete(f"{BASE}/{lid}/reset/draft").json()["draft"] is None
    assert client.delete(f"{BASE}/{lid}/reset/draft").status_code == 404
    actions = [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)]
    assert actions == ["game_reset.draft_saved", "game_reset.draft_saved",
                       "game_reset.draft_discarded"]


def test_invalid_settings_name_each_field(api):
    client, db, lid = api
    r = client.put(f"{BASE}/{lid}/reset/draft",
                   json={"settings": {"turns_per_day": 99, "attack_damage": "N"}})
    assert r.status_code == 422
    assert {d["loc"][-1] for d in r.json()["detail"]} == {"turns_per_day", "attack_damage"}
    assert db.query(GameReset).count() == 0


def test_a_draft_below_the_usual_minimum_saves_with_its_warning(api):
    client, _, lid = api
    r = client.put(f"{BASE}/{lid}/reset/draft", json={"settings": {"max_region_purchase": 0}})
    assert r.status_code == 200, r.text
    assert [w["field"] for w in r.json()["draft"]["warnings"]] == ["max_region_purchase"]


def test_fe_league_gets_fe_fields(api):
    client, db, _ = api
    fe = League(league_id="015", game_type="F", name="015F", is_active=True)
    db.add(fe)
    db.commit()
    body = client.get(f"{BASE}/{fe.id}/reset").json()
    assert [f["label"] for f in body["fields"]] == [e[0] for e in FE_EDITOR]


def test_ib_league_gets_ib_fields(api):
    client, db, _ = api
    ib = League(league_id="900", game_type="I", name="900I", is_active=True)
    db.add(ib)
    db.commit()
    body = client.get(f"{BASE}/{ib.id}/reset").json()
    kinds = {f["id"]: f["kind"] for f in body["fields"]}
    assert len(kinds) == 41 and kinds["min_board_version"] == "text"
    assert body["defaults"]["join_date"] is None


def test_one_draft_per_league_in_the_database(db_session, sample_league):
    db_session.add(GameReset(league_id=sample_league.id, status="draft", settings={}))
    db_session.add(GameReset(league_id=sample_league.id, status="applied", settings={}))
    db_session.commit()
    db_session.add(GameReset(league_id=sample_league.id, status="draft", settings={}))
    with pytest.raises(IntegrityError):
        db_session.commit()
