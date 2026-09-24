"""Pytest Configuration and Fixtures."""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app
from app.models.kri import ProcessArea, DataSource, KRI, KRITestStep, KRIThreshold
from app.models.financial import OrderIntake, PurchaseOrder
from app.repositories.kri_repository import KRIRepository
from app.services.plan_service import PlanService
from scripts.seed_data import seed_financial_data

# Use in-memory SQLite with StaticPool for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    """Create fresh database tables for each test function and teardown after."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()

    # Seed basic process areas and data sources
    kri_repo = KRIRepository(session)
    pa = kri_repo.get_or_create_process_area("Order to Cash", "O2C", "Order to Cash area")
    ds_sap = kri_repo.get_or_create_data_source("SAP ECC", "SAP_ECC", "ERP")
    ds_po = kri_repo.get_or_create_data_source("Red Box / PO", "RED_BOX_PO", "PO_ENGINE")

    # Seed mock financial transactions (80 records)
    seed_financial_data(session, record_count=80)

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient with overridden database dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def sample_active_kri(db_session):
    """Fixture providing a fully configured and activated primary KRI."""
    from scripts.seed_kri import seed_kri_configuration
    kri_id = seed_kri_configuration(db_session)
    kri_repo = KRIRepository(db_session)
    return kri_repo.get_kri_by_id(kri_id)
