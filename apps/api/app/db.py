import datetime
import json
import uuid
from typing import Any, Generator

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
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )

    jobs = relationship("Job", back_populates="dataset", cascade="all, delete-orphan")
    forecasts = relationship(
        "ProductForecast", back_populates="dataset", cascade="all, delete-orphan"
    )
    chat_messages = relationship(
        "ChatMessage", back_populates="dataset", cascade="all, delete-orphan"
    )

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
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )

    dataset = relationship("Dataset", back_populates="jobs")


class ProductForecast(Base):
    __tablename__ = "product_forecasts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
    product_id = Column(String(255), nullable=False)
    series_class = Column(String(50), nullable=False)
    chosen_model = Column(String(100), nullable=False)
    point_forecast_json = Column(Text, nullable=False)
    forecast_dates_json = Column(Text, nullable=False)
    quantiles_json = Column(Text, nullable=False)
    backtest_table_json = Column(Text, nullable=True)
    inventory_advice_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False
    )

    dataset = relationship("Dataset", back_populates="forecasts")

    @property
    def point_forecast(self) -> Any:
        return json.loads(self.point_forecast_json) if self.point_forecast_json else []

    @point_forecast.setter
    def point_forecast(self, val: Any) -> None:
        self.point_forecast_json = json.dumps(val)

    @property
    def forecast_dates(self) -> Any:
        return json.loads(self.forecast_dates_json) if self.forecast_dates_json else []

    @forecast_dates.setter
    def forecast_dates(self, val: Any) -> None:
        self.forecast_dates_json = json.dumps(val)

    @property
    def quantiles(self) -> Any:
        return json.loads(self.quantiles_json) if self.quantiles_json else {}

    @quantiles.setter
    def quantiles(self, val: Any) -> None:
        self.quantiles_json = json.dumps(val)

    @property
    def backtest_table(self) -> Any:
        return json.loads(self.backtest_table_json) if self.backtest_table_json else []

    @backtest_table.setter
    def backtest_table(self, val: Any) -> None:
        self.backtest_table_json = json.dumps(val) if val is not None else None

    @property
    def inventory_advice(self) -> Any:
        return json.loads(self.inventory_advice_json) if self.inventory_advice_json else None

    @inventory_advice.setter
    def inventory_advice(self, val: Any) -> None:
        self.inventory_advice_json = json.dumps(val) if val is not None else None


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id = Column(String(36), ForeignKey("datasets.id"), nullable=False)
    role = Column(String(20), nullable=False)  # "user" or "assistant" or "system"
    content = Column(Text, nullable=False)
    card_type = Column(String(50), nullable=True)  # e.g. "health_summary", "forecast_proof", "inventory_decision"
    payload_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False
    )

    dataset = relationship("Dataset", backref="chat_messages")
    dataset = relationship("Dataset", back_populates="chat_messages")

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json) if self.payload_json else None

    @payload.setter
    def payload(self, val: Any) -> None:
        self.payload_json = json.dumps(val) if val is not None else None


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
