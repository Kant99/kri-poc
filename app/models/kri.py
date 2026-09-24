"""SQLAlchemy Models for KRI Configurations, Metadata, Steps, and Governance."""

import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Float,
    DateTime,
    ForeignKey,
    Table,
    Text,
)
from sqlalchemy.orm import relationship
from app.core.database import Base

# Many-to-Many Association Table: KRI <-> DataSource
kri_data_sources = Table(
    "kri_data_sources",
    Base.metadata,
    Column("kri_id", Integer, ForeignKey("kris.id", ondelete="CASCADE"), primary_key=True),
    Column("data_source_id", Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), primary_key=True),
)


class ProcessArea(Base):
    """Process Area classification (e.g. O2C, P2P, R2R)."""
    __tablename__ = "process_areas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    kris = relationship("KRI", back_populates="process_area")


class DataSource(Base):
    """Catalog of enterprise source systems (e.g. SAP ECC, Red Box PO)."""
    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    system_type = Column(String(50), nullable=False)  # ERP, CRM, STORAGE, PO_ENGINE
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    kris = relationship("KRI", secondary=kri_data_sources, back_populates="data_sources")


class KRI(Base):
    """Key Risk Indicator (KRI) definition and state model."""
    __tablename__ = "kris"

    id = Column(Integer, primary_key=True, index=True)
    identifier = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    process_area_id = Column(Integer, ForeignKey("process_areas.id"), nullable=False)
    indicator_type = Column(String(50), nullable=False, default="LEADING")  # LEADING, LAGGING
    risk_description = Column(Text, nullable=False)
    end_goal = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default="DRAFT", index=True)  # DRAFT, IN_ASSESSMENT, ACTIVE, INACTIVE, ARCHIVED
    owner = Column(String(100), nullable=True, default="Internal Audit")
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    validated_at = Column(DateTime, nullable=True)

    process_area = relationship("ProcessArea", back_populates="kris")
    data_sources = relationship("DataSource", secondary=kri_data_sources, back_populates="kris")
    test_steps = relationship("KRITestStep", back_populates="kri", cascade="all, delete-orphan", order_by="KRITestStep.step_number")
    thresholds = relationship("KRIThreshold", back_populates="kri", cascade="all, delete-orphan")
    schedules = relationship("KRISchedule", back_populates="kri", cascade="all, delete-orphan")
    reviewers = relationship("Reviewer", back_populates="kri", cascade="all, delete-orphan")
    audit_runs = relationship("AuditRun", back_populates="kri")


class KRITestStep(Base):
    """Administrator-defined natural language test step for a KRI."""
    __tablename__ = "kri_test_steps"

    id = Column(Integer, primary_key=True, index=True)
    kri_id = Column(Integer, ForeignKey("kris.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    instruction = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)

    kri = relationship("KRI", back_populates="test_steps")


class KRIThreshold(Base):
    """Configured comparison thresholds for exception detection."""
    __tablename__ = "kri_thresholds"

    id = Column(Integer, primary_key=True, index=True)
    kri_id = Column(Integer, ForeignKey("kris.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    threshold_type = Column(String(50), nullable=False)  # PERCENTAGE_DIFFERENCE, ABSOLUTE_DIFFERENCE
    operator = Column(String(10), nullable=False, default=">")  # >, >=, <, <=, ==, !=
    threshold_value = Column(Float, nullable=False)
    key = Column(String(100), nullable=True)
    unit = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)

    kri = relationship("KRI", back_populates="thresholds")


class KRISchedule(Base):
    """Scheduling and cadence configuration."""
    __tablename__ = "kri_schedules"

    id = Column(Integer, primary_key=True, index=True)
    kri_id = Column(Integer, ForeignKey("kris.id", ondelete="CASCADE"), nullable=False, index=True)
    run_frequency = Column(String(50), nullable=False, default="DAILY")  # DAILY, WEEKLY, MONTHLY, QUARTERLY
    fetch_data_delay_days = Column(Integer, nullable=False, default=1)
    align_to_close_calendar = Column(Boolean, default=False, nullable=False)
    population_percentage = Column(Float, nullable=False, default=100.0)
    custom_date = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)

    kri = relationship("KRI", back_populates="schedules")


class Reviewer(Base):
    """Reviewer and Human-in-the-loop configuration."""
    __tablename__ = "reviewers"

    id = Column(Integer, primary_key=True, index=True)
    kri_id = Column(Integer, ForeignKey("kris.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_name = Column(String(150), nullable=False)
    reviewer_email = Column(String(255), nullable=False)
    human_review_setting = Column(String(50), nullable=False, default="ALL_EXCEPTIONS")  # ALL_EXCEPTIONS, HIGH_SEVERITY_ONLY, SAMPLING
    lifecycle_status = Column(String(50), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    kri = relationship("KRI", back_populates="reviewers")
