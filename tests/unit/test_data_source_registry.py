"""Unit Tests for the capability registry, source resolver and plan service.

These cover the two invariants that matter most: the manifest is derived from real models
(rather than hand-listed), and an unresolvable data source is a hard error rather than a
silent default.
"""

import pytest

from app.models.kri import DataSource
from app.repositories.kri_repository import KRIRepository
from app.schemas.kri import PlannedStepItem, StructuredPlan
from app.services.data_source_bootstrap import ORDER_INTAKE, PURCHASE_ORDER
from app.services.data_source_registry import (
    DataSourceRegistry,
    EntityDescriptor,
    FallbackMatch,
    build_capability_manifest,
    entity_registry,
    source_mention_variants,
)
from app.services.plan_interpreter import InterpretationError, PlanInterpreter
from app.services.plan_service import PlanService
from app.services.source_resolver import DataSourceResolver, SourceResolutionError


# --- Manifest is derived, not hand-written -------------------------------------


def test_manifest_fields_are_derived_from_the_model(sample_active_kri):
    """Columns come from SQLAlchemy metadata, so the manifest cannot drift from the table."""
    manifest = build_capability_manifest(sample_active_kri)
    sources = {s["code"]: s for s in manifest["data_sources"]}

    assert set(sources) == {"SAP_ECC", "RED_BOX_PO"}

    order_entity = sources["SAP_ECC"]["entities"][0]
    assert order_entity["entity_code"] == ORDER_INTAKE
    field_names = {f["name"] for f in order_entity["fields"]}
    assert {"order_id", "order_amount", "order_date", "customer_name", "po_reference"} <= field_names
    # Internal columns are never exposed.
    assert "created_at" not in field_names and "id" not in field_names

    # Roles are derived, so the interpreter can map "amount" onto a real column.
    amount_field = next(f for f in order_entity["fields"] if f["name"] == "order_amount")
    assert "amount" in amount_field["role"]
    assert "match_key" in next(f for f in order_entity["fields"] if f["name"] == "order_id")["role"]


def test_manifest_reports_unavailable_assigned_sources(db_session, sample_active_kri):
    """A KRI assigned a source with no bindings surfaces it instead of hiding it (R8)."""
    repo = KRIRepository(db_session)
    sapiens = repo.get_or_create_data_source("Sapiens", "SAPIENS", "POLICY_CORE", is_queryable=False)
    repo.set_data_sources(sample_active_kri.id, [sapiens.id])

    reloaded = repo.get_kri_by_id(sample_active_kri.id)
    manifest = build_capability_manifest(reloaded)

    assert manifest["data_sources"] == []
    assert [u["code"] for u in manifest["unbound_assigned_sources"]] == ["SAPIENS"]


def test_match_fields_are_the_actual_column_intersection():
    assert entity_registry.match_fields_between(ORDER_INTAKE, PURCHASE_ORDER) == ["order_id"]


def test_fallback_match_is_registered_per_entity_pair():
    fallback = entity_registry.fallback_match_for(ORDER_INTAKE, PURCHASE_ORDER)
    assert fallback is not None
    assert (fallback.left_field, fallback.right_field) == ("po_reference", "po_id")


def test_source_mention_variants_cover_common_spellings(sample_active_kri):
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    variants = source_mention_variants(sap)
    assert "sap ecc" in variants
    assert "sap_ecc" in variants
    assert "sap" in variants


# --- Resolver: mention scan and hard errors -------------------------------------


def _two_source_kri(db_session, kri_id):
    return KRIRepository(db_session).get_kri_by_id(kri_id)


def test_resolver_resolves_named_source_and_entity(sample_active_kri):
    resolution = DataSourceResolver().resolve(
        sample_active_kri, "Extract the population of Order Intake records from SAP ECC for the audit period."
    )
    assert resolution.data_source_code == "SAP_ECC"
    assert resolution.entity_code == ORDER_INTAKE
    assert "SAP_ECC" in resolution.selector_reason


def test_resolver_resolves_entity_only_mention(sample_active_kri):
    resolution = DataSourceResolver().resolve(sample_active_kri, "Retrieve all purchase orders for the period.")
    assert resolution.data_source_code == "RED_BOX_PO"
    assert resolution.entity_code == PURCHASE_ORDER


def test_resolver_errors_when_nothing_is_named_and_several_bindings_exist(sample_active_kri):
    """Two queryable sources and no name in the text is genuinely ambiguous (R4)."""
    with pytest.raises(SourceResolutionError) as exc:
        DataSourceResolver().resolve(sample_active_kri, "Gather the records for the period.")
    assert "does not identify which data source to read" in str(exc.value)
    assert set(exc.value.candidates) == {"SAP_ECC", "RED_BOX_PO"}


def test_resolver_uses_the_sole_available_binding_when_nothing_is_named(db_session, sample_active_kri):
    """With a single assigned binding, an unnamed step still resolves unambiguously."""
    repo = KRIRepository(db_session)
    po = next(ds for ds in sample_active_kri.data_sources if ds.code == "RED_BOX_PO")
    repo.set_data_sources(sample_active_kri.id, [po.id])
    reloaded = repo.get_kri_by_id(sample_active_kri.id)

    resolution = DataSourceResolver().resolve(reloaded, "Gather the records for the period.")
    assert resolution.data_source_code == "RED_BOX_PO"
    assert resolution.entity_code == "PURCHASE_ORDER"
    assert "only assigned source" in resolution.selector_reason


def test_resolver_refuses_a_source_that_exposes_several_entities(
    db_session, sample_active_kri
):
    """Naming a source is not enough when it carries more than one entity (R6).

    ``SAP_ECC`` exposes the order intake population *and* its debookings, so a step that says
    only "from SAP ECC" must ask for the entity instead of picking one.
    """
    repo = KRIRepository(db_session)
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    repo.set_data_sources(sample_active_kri.id, [sap.id])
    reloaded = repo.get_kri_by_id(sample_active_kri.id)

    with pytest.raises(SourceResolutionError) as exc:
        DataSourceResolver().resolve(reloaded, "Extract the population from SAP ECC.")
    detail = str(exc.value)
    assert "ORDER_INTAKE" in detail and "OI_DEBOOKING" in detail


def test_resolver_rejects_an_unassigned_source(sample_active_kri, db_session):
    """A step naming a source the KRI does not use is a hard error (R8)."""
    repo = KRIRepository(db_session)
    repo.get_or_create_data_source("Sapiens", "SAPIENS", "POLICY_CORE", is_queryable=False)
    catalog = repo.list_all_data_sources()

    # The KRI keeps only SAP_ECC, so Sapiens is catalogued but unassigned.
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    repo.set_data_sources(sample_active_kri.id, [sap.id])
    reloaded = repo.get_kri_by_id(sample_active_kri.id)

    with pytest.raises(SourceResolutionError) as exc:
        DataSourceResolver().resolve(
            reloaded, "Extract policies from Sapiens for the period.", catalog_sources=catalog
        )
    assert "not assigned" in str(exc.value)
    assert "SAPIENS" in str(exc.value)


def test_resolver_rejects_an_unavailable_source(db_session, sample_active_kri):
    """An assigned source with no entity binding cannot be read (R8)."""
    repo = KRIRepository(db_session)
    sapiens = repo.get_or_create_data_source("Sapiens", "SAPIENS", "POLICY_CORE", is_queryable=False)
    repo.set_data_sources(sample_active_kri.id, [sapiens.id])
    reloaded = repo.get_kri_by_id(sample_active_kri.id)

    with pytest.raises(SourceResolutionError) as exc:
        DataSourceResolver().resolve(reloaded, "Extract data from Sapiens for the period.")
    assert "no queryable entity" in str(exc.value)


def test_resolver_rejects_an_ambiguous_entity_mention(db_session, sample_active_kri):
    """The same entity exposed by two assigned sources is genuinely ambiguous."""
    repo = KRIRepository(db_session)
    second = repo.get_or_create_data_source("Legacy SAP", "SAP_LEGACY", "ERP", is_queryable=True)
    repo.set_entity_bindings(second.id, [(ORDER_INTAKE, True)])
    assigned_ids = [ds.id for ds in repo.get_kri_by_id(sample_active_kri.id).data_sources] + [second.id]
    repo.set_data_sources(sample_active_kri.id, assigned_ids)
    reloaded = repo.get_kri_by_id(sample_active_kri.id)

    resolver = DataSourceResolver(db_bindings=repo.bindings_by_code())
    with pytest.raises(SourceResolutionError) as exc:
        resolver.resolve(reloaded, "Retrieve all customer orders for the period.")
    assert "multiple assigned data sources" in str(exc.value)
    assert set(exc.value.candidates) == {"SAP_ECC", "SAP_LEGACY"}


def test_db_bindings_take_precedence_over_registry_defaults(db_session, sample_active_kri):
    """A binding added in the database is honoured, even if the registry default differs."""
    repo = KRIRepository(db_session)
    sapiens = repo.get_or_create_data_source("Sapiens", "SAPIENS", "POLICY_CORE", is_queryable=False)
    repo.set_entity_bindings(sapiens.id, [(ORDER_INTAKE, True)])

    bindings = repo.bindings_by_code()
    assert bindings["SAPIENS"] == [ORDER_INTAKE]

    resolver = DataSourceResolver(db_bindings=bindings)
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    repo.set_data_sources(sample_active_kri.id, [sap.id, sapiens.id])
    reloaded = repo.get_kri_by_id(sample_active_kri.id)

    resolution = resolver.resolve(reloaded, "Extract the population of Order Intake records from Sapiens.")
    assert resolution.data_source_code == "SAPIENS"
    assert resolution.entity_code == ORDER_INTAKE


def test_resolver_honours_an_explicit_pin(sample_active_kri):
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    resolution = DataSourceResolver().resolve(
        sample_active_kri, "Gather records.", pinned_source_id=sap.id, pinned_entity_code=ORDER_INTAKE
    )
    assert resolution.origin == "PINNED"
    assert resolution.data_source_code == "SAP_ECC"


def test_resolver_rejects_a_pin_to_an_unbound_entity(sample_active_kri):
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    with pytest.raises(SourceResolutionError) as exc:
        DataSourceResolver().resolve(
            sample_active_kri, "Gather records.", pinned_source_id=sap.id, pinned_entity_code=PURCHASE_ORDER
        )
    assert "does not expose" in str(exc.value)


# --- Registry contract ----------------------------------------------------------


def test_registry_rejects_an_unknown_entity():
    with pytest.raises(Exception):
        entity_registry.get_entity("NOT_AN_ENTITY")


def test_registry_rejects_binding_an_unregistered_entity():
    registry = DataSourceRegistry()
    with pytest.raises(Exception):
        registry.bind_source("SOME_SOURCE", "NOT_AN_ENTITY")


# --- PlanService validation -----------------------------------------------------


def test_validate_requires_a_mandatory_extraction_stage(sample_active_kri):
    plan = StructuredPlan(
        kri_id=sample_active_kri.id,
        steps=[PlannedStepItem(step_number=1, operation="MATCH_RECORDS", tool_name="compare_records",
                               parameters={"left_alias": "a", "right_alias": "b", "comparison_alias": "c",
                                           "match_configuration": {"left_field": "order_id", "right_field": "order_id"}})],
    )
    result = PlanService.validate_plan(sample_active_kri, plan)
    assert not result.is_valid
    assert any("EXTRACT_POPULATION" in e for e in result.errors)


def test_validate_rejects_an_unregistered_tool(sample_active_kri):
    plan = StructuredPlan(
        kri_id=sample_active_kri.id,
        steps=[PlannedStepItem(step_number=1, operation="EXTRACT_POPULATION", tool_name="drop_all_tables",
                               parameters={"data_source_code": "SAP_ECC", "entity_code": ORDER_INTAKE,
                                           "dataset_alias": "pop"})],
    )
    result = PlanService.validate_plan(sample_active_kri, plan)
    assert not result.is_valid
    assert any("unregistered tool" in e for e in result.errors)


def test_validate_rejects_a_data_source_outside_the_manifest(sample_active_kri):
    """The manifest is a fence: a plan cannot read an unassigned source (R7)."""
    plan = StructuredPlan(
        kri_id=sample_active_kri.id,
        steps=[PlannedStepItem(step_number=1, operation="EXTRACT_POPULATION", tool_name="fetch_financial_data",
                               parameters={"data_source_code": "SAPIENS", "entity_code": ORDER_INTAKE,
                                           "dataset_alias": "pop"},
                               data_source_code="SAPIENS", entity_code=ORDER_INTAKE)],
    )
    result = PlanService.validate_plan(sample_active_kri, plan)
    assert not result.is_valid
    assert any("not an assigned, queryable data source" in e for e in result.errors)


def test_validate_rejects_mismatched_match_fields(sample_active_kri):
    plan = StructuredPlan(
        kri_id=sample_active_kri.id,
        steps=[
            PlannedStepItem(step_number=1, operation="EXTRACT_POPULATION", tool_name="fetch_financial_data",
                            parameters={"data_source_code": "SAP_ECC", "entity_code": ORDER_INTAKE,
                                        "dataset_alias": "pop_order_intake"},
                            data_source_code="SAP_ECC", entity_code=ORDER_INTAKE),
            PlannedStepItem(step_number=2, operation="MATCH_RECORDS", tool_name="compare_records",
                            parameters={"left_alias": "pop_order_intake", "right_alias": "pop_purchase_order",
                                        "comparison_alias": "comparison",
                                        "match_configuration": {"left_field": "order_id", "right_field": "po_id"}}),
        ],
    )
    result = PlanService.validate_plan(sample_active_kri, plan)
    assert not result.is_valid
    assert any("both sides must use the same field name" in e for e in result.errors)


def test_describe_operations_lists_every_approved_operation():
    operations = {o["operation"] for o in PlanService.describe_operations()}
    assert "EXTRACT_POPULATION" in operations
    assert "MATCH_RECORDS" in operations
    assert "CALCULATE_METRICS" in operations
    assert "BUILD_EVIDENCE" in operations
    assert "PREPARE" in operations


def test_required_stages_are_injected(sample_active_kri):
    plan = StructuredPlan(
        kri_id=sample_active_kri.id,
        steps=[PlannedStepItem(step_number=1, operation="EXTRACT_POPULATION", tool_name="fetch_financial_data",
                               parameters={"data_source_code": "SAP_ECC", "entity_code": ORDER_INTAKE,
                                           "dataset_alias": "pop"})],
    )
    plan = PlanService.inject_required_stages(plan)
    operations = [s.operation for s in plan.steps]
    assert "CALCULATE_METRICS" in operations
    assert "BUILD_EVIDENCE" in operations
    assert [s.origin for s in plan.steps if s.operation == "CALCULATE_METRICS"] == ["INJECTED"]


def test_default_exception_rules_match_the_previous_hardcoded_behaviour():
    rules = {r.rule: r for r in PlanService.build_exception_rules()}
    assert rules["MISSING_COUNTERPART"].source_bucket == "missing_pos"
    assert rules["MISSING_COUNTERPART"].severity_override == "HIGH"
    assert rules["VALUE_DIFFERENCE_EXCEEDS_THRESHOLD"].source_bucket == "matched_pairs"
    assert rules["VALUE_DIFFERENCE_EXCEEDS_THRESHOLD"].only_if_threshold_breached is True
    assert rules["AMBIGUOUS_COUNTERPART"].source_bucket == "ambiguous_matches"
