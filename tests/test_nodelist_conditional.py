"""Conditional-request handling for the nodelist endpoint.

Clients poll the hub every couple of minutes but nodelists change rarely, so
the endpoint answers 304 to a conditional request. These tests cover the
decision logic and, importantly, pin our ETag to the one Starlette's
FileResponse generates - the 304 path and the 200 path must agree on the value,
or clients would re-download forever while believing they were caching.
"""

from email.utils import formatdate

import pytest
from starlette.datastructures import Headers

from backend.api.service.leagues import _is_not_modified


class FakeRequest:
    """Just enough Request for _is_not_modified."""

    def __init__(self, **headers):
        self.headers = Headers({k.replace("_", "-"): v for k, v in headers.items()})


ETAG = '"8f14e45fceea167a5a36dedd4bea2543"'
MTIME = 1_700_000_000.0


def test_etag_matches_starlette_fileresponse(tmp_path):
    """The 200 and 304 paths must produce the same ETag from the same stat.

    FileResponse computes it from st_mtime and st_size; if our copy of that
    formula drifts, every conditional request silently misses.
    """
    from starlette.responses import FileResponse

    from backend.api.service import leagues

    target = tmp_path / "BRNODES.555"
    target.write_bytes(b"2\nTest BBS\n1:2/3\n\n\n\n\n")
    stat = target.stat()

    response = FileResponse(target)
    response.set_stat_headers(stat)
    starlette_etag = response.headers["etag"]

    ours = leagues.hashlib.md5(
        f"{stat.st_mtime}-{stat.st_size}".encode(), usedforsecurity=False
    ).hexdigest()

    assert f'"{ours}"' == starlette_etag


def test_no_conditional_headers_is_a_full_download():
    assert _is_not_modified(FakeRequest(), ETAG, MTIME) is False


def test_missing_request_is_a_full_download():
    assert _is_not_modified(None, ETAG, MTIME) is False


def test_matching_etag_is_not_modified():
    assert _is_not_modified(FakeRequest(if_none_match=ETAG), ETAG, MTIME) is True


def test_stale_etag_is_modified():
    assert _is_not_modified(FakeRequest(if_none_match='"stale"'), ETAG, MTIME) is False


def test_weak_etag_still_matches():
    """A cache may weaken the tag to W/"..."; the opaque part is what counts."""
    assert _is_not_modified(FakeRequest(if_none_match=f"W/{ETAG}"), ETAG, MTIME) is True


def test_etag_list_matches_any_member():
    header = f'"other", {ETAG}'
    assert _is_not_modified(FakeRequest(if_none_match=header), ETAG, MTIME) is True


def test_wildcard_etag_matches():
    assert _is_not_modified(FakeRequest(if_none_match="*"), ETAG, MTIME) is True


def test_if_none_match_wins_over_if_modified_since():
    """RFC 9110: when If-None-Match is present, If-Modified-Since is ignored.

    This is what protects a client whose clock is skewed but whose ETag is good.
    """
    request = FakeRequest(
        if_none_match='"stale"',
        if_modified_since=formatdate(MTIME + 10_000, usegmt=True),
    )
    assert _is_not_modified(request, ETAG, MTIME) is False


def test_if_modified_since_newer_than_file_is_not_modified():
    request = FakeRequest(if_modified_since=formatdate(MTIME + 60, usegmt=True))
    assert _is_not_modified(request, ETAG, MTIME) is True


def test_if_modified_since_older_than_file_is_modified():
    request = FakeRequest(if_modified_since=formatdate(MTIME - 60, usegmt=True))
    assert _is_not_modified(request, ETAG, MTIME) is False


def test_if_modified_since_same_second_is_not_modified():
    """HTTP dates are whole seconds, so sub-second mtime must not defeat a match."""
    request = FakeRequest(if_modified_since=formatdate(MTIME, usegmt=True))
    assert _is_not_modified(request, ETAG + "x", MTIME + 0.4) is True


@pytest.mark.parametrize("value", ["not a date", "", "Tue, 99 Xxx 2026"])
def test_unparseable_if_modified_since_is_a_full_download(value):
    """Garbage in the header must fall back to sending the file, never to 304."""
    assert _is_not_modified(FakeRequest(if_modified_since=value), ETAG, MTIME) is False
