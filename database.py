import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use environment variable DATABASE_URL for Railway / Render / PostgreSQL deployment, default to SQLite locally
raw_url = os.getenv("DATABASE_URL")
if not raw_url or not raw_url.strip():
    DATABASE_URL = "sqlite:///./eventsphere.db"
else:
    DATABASE_URL = raw_url.strip()

# Cloud providers (Heroku, Render, older Railway templates) may supply postgres:// or postgresql://
# Explicitly use postgresql+psycopg2:// driver to guarantee compatibility with psycopg2
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# SQLite needs check_same_thread=False for FastAPI multithreaded requests
is_sqlite = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine_kwargs = {
    "connect_args": connect_args,
    "pool_pre_ping": True,
    "echo": False,
}

# For PostgreSQL on cloud platforms like Railway, enable connection pool pre-ping & recycle
# to prevent "server closed the connection unexpectedly" 500 errors after idle periods.
if not is_sqlite:
    engine_kwargs.update({
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20,
    })

engine = create_engine(
    DATABASE_URL,
    **engine_kwargs
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def get_db():
    """Dependency that provides a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
