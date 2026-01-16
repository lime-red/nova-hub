"""
Database session management for Nova Hub

Handles database initialization and provides session dependencies for FastAPI.
"""

from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Database engine and session factory
engine = None
SessionLocal = None


def init_database(database_url: str) -> any:
    """
    Initialize database connection

    Args:
        database_url: SQLAlchemy database URL (e.g., "sqlite:////path/to/db.db")

    Returns:
        SQLAlchemy engine instance
    """
    global engine, SessionLocal

    connect_args = {}
    if "sqlite" in database_url:
        connect_args["check_same_thread"] = False

    engine = create_engine(database_url, connect_args=connect_args)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return engine


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency for database sessions

    Yields a database session and ensures it's closed after the request.

    Usage:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session() -> Session:
    """
    Get a database session directly (for non-FastAPI usage)

    Remember to close the session when done!

    Usage:
        db = get_session()
        try:
            # do stuff
        finally:
            db.close()
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    return SessionLocal()
