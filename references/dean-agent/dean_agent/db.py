from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from dean_agent.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
deanery_read_engine = create_engine(settings.deanery_read_url, pool_pre_ping=True) if settings.deanery_read_url else None
DeaneryReadSession = sessionmaker(deanery_read_engine, expire_on_commit=False) if deanery_read_engine else None
