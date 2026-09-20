import datetime
import json
import uuid
from collections.abc import Generator
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from apps.api.app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    parquet_path = Column(String(512), nullable=True)
    status = Column(String(50), default="uploaded", nullable=False)
    row_count = Column(Integer, nullable=True)
    product_count = Column(Integer, nullable=True)
    column_mapping_json = Column(Text, nullable=True)
    health_report_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )

    jobs = relationship("Job", back_populates="dataset", cascade="all, delete-orphan")

    @property
    def column_mapping(self) -> Any:
        return json.loads(self.column_mapping_json) if self.column_mapping_json else None

    @column_mapping.setter
    def column_mapping(self, value: Any) -> None:
        self.column_mapping_json = json.dumps(value) if value is not None else None

    @property
    def health_report(self) -> Any:
        return json.loads(self.health_report_json) if self.health_report_json else None

    @health_report.setter
    def health_report(self, value: Any) -> None:
        self.health_report_json = json.dumps(value) if value is not None else None


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
    job_type = Column(String(50), nullable=False)
    status = Column(String(50), default="queued", nullable=False)
    progress = Column(Integer, default=0, nullable=False)
    current_step = Column(String(255), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )

    dataset = relationship("Dataset", back_populates="jobs")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
