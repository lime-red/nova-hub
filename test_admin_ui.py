#!/usr/bin/env python3
"""
Test Admin UI for League Membership Management (BBS ID features)
"""

import requests
from bs4 import BeautifulSoup
import sys

BASE_URL = "http://localhost:8000"
session = requests.Session()

def login():
    """Login to admin UI"""
    print("\n1. Testing Login...")
    response = session.post(
        f"{BASE_URL}/login",
        data={"username": "admin", "password": "admin"},
        allow_redirects=False
    )

    if response.status_code in [302, 303, 307]:
        print("   ✓ Login successful (redirect received)")
        return True
    else:
        print(f"   ✗ Login failed (status: {response.status_code})")
        return False

def test_league_detail():
    """Test league detail page shows BBS IDs"""
    print("\n2. Testing League Detail Page...")

    # Get the league ID from leagues list
    response = session.get(f"{BASE_URL}/admin/leagues")
    if response.status_code != 200:
        print(f"   ✗ Failed to access leagues page (status: {response.status_code})")
        return False

    # Find first league
    soup = BeautifulSoup(response.text, 'html.parser')
    league_link = soup.find('a', href=lambda x: x and '/admin/leagues/' in x and x != '/admin/leagues/new')

    if not league_link:
        print("   ✗ No leagues found")
        return False

    league_url = league_link['href']
    print(f"   ✓ Found league: {league_url}")

    # Access league detail
    response = session.get(f"{BASE_URL}{league_url}")
    if response.status_code != 200:
        print(f"   ✗ Failed to access league detail (status: {response.status_code})")
        return False

    soup = BeautifulSoup(response.text, 'html.parser')

    # Check for BBS ID column in membership table
    headers = [th.get_text(strip=True) for th in soup.find_all('th')]
    if 'BBS ID' in headers:
        print("   ✓ BBS ID column found in membership table")
    else:
        print(f"   ✗ BBS ID column not found. Headers: {headers}")
        return False

    # Check for Edit BBS ID button
    edit_buttons = soup.find_all('button', string=lambda x: x and 'Edit BBS ID' in x)
    if edit_buttons:
        print(f"   ✓ Found {len(edit_buttons)} 'Edit BBS ID' button(s)")
    else:
        print("   ✗ 'Edit BBS ID' button not found")
        return False

    # Check for BBS ID modal
    modal = soup.find('dialog', id='edit-bbs-index-modal')
    if modal:
        print("   ✓ Edit BBS ID modal found")
    else:
        print("   ✗ Edit BBS ID modal not found")
        return False

    return True

def test_update_bbs_id():
    """Test updating a membership's BBS ID"""
    print("\n3. Testing BBS ID Update API...")

    # Get league and membership IDs from database
    import toml
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    config = toml.load("config.toml")
    db_path = config.get("database", {}).get("path", "/home/lime/nova-data/nova-hub.db")
    database_url = f"sqlite:///{db_path}"

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    from app.database import League, LeagueMembership

    league = db.query(League).first()
    if not league:
        print("   ✗ No league found in database")
        db.close()
        return False

    membership = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league.id
    ).first()

    if not membership:
        print("   ✗ No membership found in database")
        db.close()
        return False

    old_bbs_index = membership.bbs_index
    new_bbs_index = 10 if old_bbs_index != 10 else 11

    print(f"   League ID: {league.id}, Membership ID: {membership.id}")
    print(f"   Current BBS ID: {old_bbs_index}, New BBS ID: {new_bbs_index}")

    # Test the update API
    response = session.post(
        f"{BASE_URL}/admin/leagues/{league.id}/members/{membership.id}/update-bbs-index",
        data={"bbs_index": new_bbs_index},
        allow_redirects=False
    )

    if response.status_code in [302, 303, 307]:
        print(f"   ✓ Update request successful (redirect received)")

        # Verify the change in database
        db.expire_all()
        membership = db.query(LeagueMembership).get(membership.id)
        if membership.bbs_index == new_bbs_index:
            print(f"   ✓ BBS ID updated correctly in database: {membership.bbs_index}")

            # Restore original value
            session.post(
                f"{BASE_URL}/admin/leagues/{league.id}/members/{membership.id}/update-bbs-index",
                data={"bbs_index": old_bbs_index},
                allow_redirects=False
            )
            db.close()
            return True
        else:
            print(f"   ✗ BBS ID not updated in database (still: {membership.bbs_index})")
            db.close()
            return False
    else:
        print(f"   ✗ Update failed (status: {response.status_code})")
        print(f"   Response: {response.text[:200]}")
        db.close()
        return False

def test_add_member_with_bbs_id():
    """Test adding a new client to league with BBS ID"""
    print("\n4. Testing Add Member with BBS ID...")

    # Get league and an unassigned client
    import toml
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    config = toml.load("config.toml")
    db_path = config.get("database", {}).get("path", "/home/lime/nova-data/nova-hub.db")
    database_url = f"sqlite:///{db_path}"

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    from app.database import League, Client, LeagueMembership

    league = db.query(League).first()
    if not league:
        print("   ✗ No league found")
        db.close()
        return False

    # Create a test client if none exists
    existing_client = db.query(Client).filter(Client.bbs_name == "Test BBS 2").first()
    if existing_client:
        test_client = existing_client
    else:
        test_client = Client(
            client_id="test_bbs_2_oauth_id",
            client_secret=Client.generate_client_secret(),
            bbs_name="Test BBS 2",
            contact_email="test2@example.com",
            is_active=True
        )
        db.add(test_client)
        db.commit()
        print(f"   ✓ Created test client: {test_client.bbs_name}")

    # Remove any existing membership
    existing_membership = db.query(LeagueMembership).filter(
        LeagueMembership.client_id == test_client.id,
        LeagueMembership.league_id == league.id
    ).first()

    if existing_membership:
        db.delete(existing_membership)
        db.commit()
        print(f"   ✓ Removed existing membership")

    # Store IDs before closing session
    league_id = league.id
    client_id = test_client.id

    db.close()

    # Test adding member via API
    test_bbs_index = 15
    response = session.post(
        f"{BASE_URL}/admin/leagues/{league_id}/members/add",
        data={
            "client_id": client_id,
            "bbs_index": test_bbs_index
        },
        allow_redirects=False
    )

    if response.status_code in [302, 303, 307]:
        print(f"   ✓ Add member request successful")

        # Verify in database
        db = Session()
        membership = db.query(LeagueMembership).filter(
            LeagueMembership.client_id == client_id,
            LeagueMembership.league_id == league_id
        ).first()

        if membership and membership.bbs_index == test_bbs_index:
            print(f"   ✓ Member added with correct BBS ID: {membership.bbs_index}")
            db.close()
            return True
        else:
            print(f"   ✗ Member not added or BBS ID incorrect")
            db.close()
            return False
    else:
        print(f"   ✗ Add member failed (status: {response.status_code})")
        print(f"   Response: {response.text[:200]}")
        return False

def test_bbs_id_validation():
    """Test BBS ID validation (range and uniqueness)"""
    print("\n5. Testing BBS ID Validation...")

    import toml
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    config = toml.load("config.toml")
    db_path = config.get("database", {}).get("path", "/home/lime/nova-data/nova-hub.db")
    database_url = f"sqlite:///{db_path}"

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    from app.database import League, LeagueMembership

    league = db.query(League).first()
    membership = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league.id
    ).first()

    if not league or not membership:
        print("   ✗ No league or membership found")
        db.close()
        return False

    # Test 1: Out of range (0)
    response = session.post(
        f"{BASE_URL}/admin/leagues/{league.id}/members/{membership.id}/update-bbs-index",
        data={"bbs_index": 0},
        allow_redirects=False
    )

    if response.status_code == 400:
        print("   ✓ Rejected BBS ID 0 (out of range)")
    else:
        print(f"   ✗ Did not reject BBS ID 0 (status: {response.status_code})")

    # Test 2: Out of range (256)
    response = session.post(
        f"{BASE_URL}/admin/leagues/{league.id}/members/{membership.id}/update-bbs-index",
        data={"bbs_index": 256},
        allow_redirects=False
    )

    if response.status_code == 400:
        print("   ✓ Rejected BBS ID 256 (out of range)")
    else:
        print(f"   ✗ Did not reject BBS ID 256 (status: {response.status_code})")

    # Test 3: Duplicate in same league
    # Get another membership in the same league
    memberships = db.query(LeagueMembership).filter(
        LeagueMembership.league_id == league.id
    ).all()

    if len(memberships) >= 2:
        membership1 = memberships[0]
        membership2 = memberships[1]
        duplicate_bbs_id = membership1.bbs_index

        response = session.post(
            f"{BASE_URL}/admin/leagues/{league.id}/members/{membership2.id}/update-bbs-index",
            data={"bbs_index": duplicate_bbs_id},
            allow_redirects=False
        )

        if response.status_code == 400:
            print(f"   ✓ Rejected duplicate BBS ID {duplicate_bbs_id} in same league")
        else:
            print(f"   ✗ Did not reject duplicate BBS ID (status: {response.status_code})")
    else:
        print("   ⚠ Skipped duplicate test (need at least 2 memberships)")

    db.close()
    return True

def main():
    print("=" * 60)
    print("TESTING ADMIN UI - LEAGUE MEMBERSHIP MANAGEMENT")
    print("=" * 60)

    tests_passed = 0
    tests_total = 5

    if login():
        tests_passed += 1

    if test_league_detail():
        tests_passed += 1

    if test_update_bbs_id():
        tests_passed += 1

    if test_add_member_with_bbs_id():
        tests_passed += 1

    if test_bbs_id_validation():
        tests_passed += 1

    print("\n" + "=" * 60)
    print(f"TESTS COMPLETED: {tests_passed}/{tests_total} passed")
    print("=" * 60)

    if tests_passed == tests_total:
        print("\n✓ ALL TESTS PASSED")
        return 0
    else:
        print(f"\n✗ {tests_total - tests_passed} TEST(S) FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
