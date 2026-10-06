"""GET /service/api/v1/me: what the hub tells a BBS about itself.

The client's connection test compares this with its config, so it must list
exactly the leagues the hub would accept packets for, with the right index.
"""

from backend.models.database import League, LeagueMembership

# The claim tests build a hub with Jersey Jam (#5 in 015B and 015F) in it.
from tests.test_claim_links import TOKEN_URL, api, bbs  # noqa: F401 (fixtures)

ME = "/service/api/v1/me"


def _token(client, secret="old-secret"):
    response = client.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                              "client_id": "jersey", "client_secret": secret})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_me_lists_the_bbs_and_its_leagues(api, bbs):
    client, _ = api

    body = client.get(ME, headers=_token(client)).json()

    assert body["client_id"] == "jersey"
    assert body["bbs_name"] == "Jersey Jam"
    assert body["leagues"] == [
        {"league_id": "015B", "game": "BRE", "number": "015", "name": "015B",
         "bbs_index": 5, "fidonet_address": "135:135/5"},
        {"league_id": "015F", "game": "FE", "number": "015", "name": "015F",
         "bbs_index": 5, "fidonet_address": "135:135/5"},
    ]


def test_me_leaves_out_inactive_memberships_and_leagues(api, bbs):
    client, db = api
    fe = db.query(League).filter(League.game_type == "F").one()
    fe.is_active = False
    db.query(LeagueMembership).filter(LeagueMembership.league_id != fe.id).update(
        {"is_active": False})
    db.commit()

    body = client.get(ME, headers=_token(client)).json()

    assert body["leagues"] == []


def test_me_needs_a_token(api, bbs):
    client, _ = api

    assert client.get(ME).status_code == 401
