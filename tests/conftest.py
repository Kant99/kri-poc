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
from scripts.seed_data import seed_financial_data

# Use in-memory SQLite with StaticPool for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def hermetic_interpreter(monkeypatch):
    """Pin step interpretation to the deterministic rules engine for the whole suite.

    The suite must never call Azure OpenAI: it would make every test slow, network-dependent
    and non-reproducible. Live LLM interpretation is exercised separately by
    ``scripts/verify_llm_interpretation.py``, which is opt-in.

    The mode is patched on the class (not the instance) because pydantic settings reject
    setting an attribute that is not a declared field.
    """
    from app.core.config import settings

    monkeypatch.setattr(type(settings), "get_interpreter_mode", lambda self: "rules", raising=False)
    yield


@pytest.fixture(scope="function")
def db_session():
    """Create fresh database tables for each test function and teardown after."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()

    # Seed process areas and the data source catalog *with entity bindings*, so a plan can
    # only ever be built against sources that actually expose queryable data.
    from app.services.data_source_bootstrap import register_all_entities

    register_all_entities()

    kri_repo = KRIRepository(session)
    pa = kri_repo.get_or_create_process_area("Order to Cash", "O2C", "Order to Cash area")
    ds_sap = kri_repo.get_or_create_data_source("SAP ECC", "SAP_ECC", "ERP", is_queryable=True)
    ds_po = kri_repo.get_or_create_data_source("Red Box / PO", "RED_BOX_PO", "PO_ENGINE", is_queryable=True)
    kri_repo.set_entity_bindings(ds_sap.id, [("ORDER_INTAKE", True), ("OI_DEBOOKING", False)])
    kri_repo.set_entity_bindings(ds_po.id, [("PURCHASE_ORDER", True)])
    # A catalog source with no queryable data, used to prove R8.
    kri_repo.get_or_create_data_source(
        "Sapiens",
        "SAPIENS",
        "POLICY_CORE",
        is_queryable=False,
        availability_note="No queryable entity is bound to this data source.",
    )

    # Seed mock financial transactions (80 records per year)
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
