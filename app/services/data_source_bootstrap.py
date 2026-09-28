"""Entity Registration Bootstrap.

Declares every queryable business entity and which catalog data source exposes it. This is
the single place where a physical table becomes a capability a plan may reference.

Adding a new source table requires only: a SQLAlchemy model, an ``EntityDescriptor`` here,
and a ``data_source_entities`` row seeded for the owning data source.
"""

from app.models.financial import (
    OrderIntake,
    OrderIntakeDebooking,
    PurchaseOrder,
    WBSElement,
    SCRMOpportunity,
    YRARevenueRecord,
    YCACostRecord,
)
from app.services.data_source_registry import EntityDescriptor, entity_registry

ORDER_INTAKE = "ORDER_INTAKE"
PURCHASE_ORDER = "PURCHASE_ORDER"
OI_DEBOOKING = "OI_DEBOOKING"
WBS_MASTER = "WBS_MASTER"
SCRM_OPPORTUNITY = "SCRM_OPPORTUNITY"
YRA_REVENUE = "YRA_REVENUE"
YCA_COST = "YCA_COST"

#: Alias used by the interpreter when it needs the entity without importing the model.
DEBOOKING_ENTITY = OI_DEBOOKING


def _order_intake() -> EntityDescriptor:
    return EntityDescriptor(
        entity_code=ORDER_INTAKE,
        model=OrderIntake,
        discriminator_column="source_system",
        date_column="order_date",
        amount_column="order_amount",
        id_column="order_id",
        record_key_field="order_id",
        match_fields=("order_id", "po_reference", "customer_name"),
        aliases=(
            "order intake",
            "order intake records",
            "customer orders",
            "customer order",
            "customers",
            "customer",
            "sales orders",
            "sales order",
            "order intake population",
            "orders",
            "order",
        ),
        population_role="population",
    )


def _purchase_order() -> EntityDescriptor:
    return EntityDescriptor(
        entity_code=PURCHASE_ORDER,
        model=PurchaseOrder,
        discriminator_column="source_system",
        date_column="po_date",
        amount_column="po_amount",
        id_column="po_id",
        record_key_field="po_id",
        match_fields=("order_id", "po_id", "vendor_id"),
        aliases=(
            "purchase orders",
            "purchase order",
            "purchase order records",
            "po records",
            "counterpart purchase orders",
            "pos",
            "po",
            "customer purchase orders",
            "customer po",
        ),
        population_role="counterparty",
    )


def _oi_debooking() -> EntityDescriptor:
    """Order Intake debookings - the reversals of previously booked order intake.

    Exposed by the same ERP as the order intake population, because a debooking is a journal
    reversal raised in the same system that recorded the original booking. Binding both to
    ``SAP_ECC`` is deliberate: it exercises the case where one data source exposes more than
    one entity, so a step that says only "SAP ECC" is genuinely ambiguous and the resolver
    has to rely on the entity wording.
    """
    return EntityDescriptor(
        entity_code=OI_DEBOOKING,
        model=OrderIntakeDebooking,
        discriminator_column="source_system",
        date_column="debooking_date",
        amount_column="debooking_amount",
        id_column="debooking_id",
        record_key_field="debooking_id",
        match_fields=("order_id", "customer_name", "debooking_id"),
        aliases=(
            "order intake debookings",
            "order intake debooking",
            "oi debookings",
            "oi debooking",
            "order intake reversals",
            "order intake debit notes",
            "debit notes",
            "debooking journal",
            "debookings",
            "debooking",
            "reversals",
            "credit notes",
        ),
        population_role="supporting",
    )


def _wbs_master() -> EntityDescriptor:
    return EntityDescriptor(
        entity_code=WBS_MASTER,
        model=WBSElement,
        discriminator_column="source_system",
        date_column="created_date",
        amount_column="budget_amount",
        id_column="wbs_code",
        record_key_field="wbs_code",
        match_fields=("wbs_code", "project_name", "customer_name"),
        aliases=(
            "wbs elements",
            "wbs element",
            "wbs master",
            "wbs records",
            "wbs",
            "work breakdown structure",
            "project wbs",
        ),
        population_role="supporting",
    )


def _scrm_opportunity() -> EntityDescriptor:
    return EntityDescriptor(
        entity_code=SCRM_OPPORTUNITY,
        model=SCRMOpportunity,
        discriminator_column="source_system",
        date_column="close_date",
        amount_column="planned_value",
        id_column="opportunity_id",
        record_key_field="opportunity_id",
        match_fields=("opportunity_id", "contract_id", "customer_name"),
        aliases=(
            "commercial opportunities",
            "commercial opportunity",
            "scrm opportunities",
            "scrm opportunity",
            "scrm contracts",
            "scrm contract",
            "contracts",
            "contract",
            "opportunities",
            "opportunity",
            "opportunity records",
            "scrm",
        ),
        population_role="supporting",
    )


def _yra_revenue() -> EntityDescriptor:
    return EntityDescriptor(
        entity_code=YRA_REVENUE,
        model=YRARevenueRecord,
        discriminator_column="source_system",
        date_column="revenue_date",
        amount_column="revenue_amount",
        id_column="revenue_id",
        record_key_field="revenue_id",
        match_fields=("revenue_id", "wbs_element", "opportunity_id", "billing_doc", "customer_name"),
        aliases=(
            "yra report",
            "yra revenue",
            "yra revenue report",
            "revenue records",
            "yra",
            "recognized revenue",
            "actual revenue",
            "revenue actuals",
        ),
        population_role="supporting",
    )


def _yca_cost() -> EntityDescriptor:
    return EntityDescriptor(
        entity_code=YCA_COST,
        model=YCACostRecord,
        discriminator_column="source_system",
        date_column="cost_date",
        amount_column="cost_amount",
        id_column="cost_id",
        record_key_field="cost_id",
        match_fields=("cost_id", "wbs_element", "cost_element", "vendor_or_person"),
        aliases=(
            "yca report",
            "yca cost",
            "yca cost report",
            "cost records",
            "yca",
            "incurred costs",
            "actual costs",
            "project costs",
        ),
        population_role="supporting",
    )


def register_all_entities() -> None:
    """Register every entity descriptor, source binding and cross-entity fallback match."""
    if entity_registry.is_registered(ORDER_INTAKE):
        return  # already bootstrapped

    entity_registry.register_entity(_order_intake())
    entity_registry.register_entity(_purchase_order())
    entity_registry.register_entity(_oi_debooking())
    entity_registry.register_entity(_wbs_master())
    entity_registry.register_entity(_scrm_opportunity())
    entity_registry.register_entity(_yra_revenue())
    entity_registry.register_entity(_yca_cost())

    entity_registry.bind_source("SAP_ECC", ORDER_INTAKE, is_primary=True)
    entity_registry.bind_source("SAP_ECC", OI_DEBOOKING, is_primary=False)
    entity_registry.bind_source("SAP_ECC", WBS_MASTER, is_primary=False)
    entity_registry.bind_source("SAP_ECC", YRA_REVENUE, is_primary=False)
    entity_registry.bind_source("SAP_ECC", YCA_COST, is_primary=False)
    entity_registry.bind_source("RED_BOX_PO", PURCHASE_ORDER, is_primary=True)
    entity_registry.bind_source("SCRM", SCRM_OPPORTUNITY, is_primary=True)

    # An order's po_reference resolves against a purchase order's own po_id.
    entity_registry.register_fallback_match(
        ORDER_INTAKE, PURCHASE_ORDER, left_field="po_reference", right_field="po_id"
    )
    # A debooking's order_id resolves against the original order intake's order_id.
    entity_registry.register_fallback_match(
        OI_DEBOOKING, ORDER_INTAKE, left_field="order_id", right_field="order_id"
    )
    # Fallback matches across WBS and Opportunities
    entity_registry.register_fallback_match(
        ORDER_INTAKE, WBS_MASTER, left_field="wbs_element", right_field="wbs_code"
    )
    entity_registry.register_fallback_match(
        YRA_REVENUE, WBS_MASTER, left_field="wbs_element", right_field="wbs_code"
    )
    entity_registry.register_fallback_match(
        YCA_COST, WBS_MASTER, left_field="wbs_element", right_field="wbs_code"
    )
    entity_registry.register_fallback_match(
        ORDER_INTAKE, SCRM_OPPORTUNITY, left_field="opportunity_id", right_field="opportunity_id"
    )


#: Maps a catalog data source code to the entity it is expected to expose. Used by the
#: seeder to materialise ``data_source_entities`` rows consistent with the registry.
DEFAULT_SOURCE_BINDINGS = {
    "SAP_ECC": (ORDER_INTAKE, True),
    "RED_BOX_PO": (PURCHASE_ORDER, True),
    "SCRM": (SCRM_OPPORTUNITY, True),
}

#: Additional (non-primary) bindings per source.
ADDITIONAL_SOURCE_BINDINGS = {
    "SAP_ECC": [
        (OI_DEBOOKING, False),
        (WBS_MASTER, False),
        (YRA_REVENUE, False),
        (YCA_COST, False),
    ],
}

#: Catalog sources that intentionally have no queryable data in this environment.
UNAVAILABLE_SOURCE_CODES = ("SAPIENS", "BLUE_PLANET", "EMS_SHAREPOINT")


register_all_entities()

