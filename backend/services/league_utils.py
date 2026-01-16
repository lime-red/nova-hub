"""Utility functions for league ID parsing and validation"""

import re
from typing import Tuple
from fastapi import HTTPException

LEAGUE_ID_PATTERN = re.compile(r'^(\d{3})([BF])$')


def parse_league_id(league_id: str) -> Tuple[str, str]:
    """
    Parse league_id from URL into components.

    Args:
        league_id: Combined league ID (e.g., "555B", "555F")

    Returns:
        Tuple of (league_number, game_type)
        Example: "555B" -> ("555", "B")

    Raises:
        HTTPException(400): If format is invalid

    Examples:
        >>> parse_league_id("555B")
        ("555", "B")
        >>> parse_league_id("123F")
        ("123", "F")
        >>> parse_league_id("555")  # raises HTTPException
    """
    match = LEAGUE_ID_PATTERN.match(league_id.upper())
    if not match:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid league_id format: '{league_id}'. "
                "Expected format: <3-digit-number><B|F> (e.g., '555B' for BRE, '555F' for FE)"
            )
        )
    return match.groups()


def format_league_id(league_number: str, game_type: str) -> str:
    """
    Format league components into a league_id.

    Args:
        league_number: Three-digit league number (e.g., "555")
        game_type: Single character game type ("B" or "F")

    Returns:
        Formatted league_id (e.g., "555B")

    Examples:
        >>> format_league_id("555", "B")
        "555B"
        >>> format_league_id("123", "F")
        "123F"
    """
    return f"{league_number}{game_type.upper()}"


def validate_league_id_format(league_id: str) -> bool:
    """
    Check if league_id matches expected format without raising exception.

    Args:
        league_id: League ID to validate

    Returns:
        True if valid, False otherwise

    Examples:
        >>> validate_league_id_format("555B")
        True
        >>> validate_league_id_format("555")
        False
    """
    return LEAGUE_ID_PATTERN.match(league_id.upper()) is not None
