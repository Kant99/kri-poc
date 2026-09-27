"""Entity Registration Bootstrap.

Declares every queryable business entity and which catalog data source exposes it. This is
the single place where a physical table becomes a capability a plan may reference.

Adding a new source table requires only: a SQLAlchemy model, an ``EntityDescriptor`` here,
and a ``data_source_entities`` row seeded for the owning data source.
"""

from app.models.financial import OrderIntake, OrderIntakeDebooking, PurchaseOrder
from app.services.data_source_registry import EntityDescriptor, entity_registry

ORDER_INTAKE = "ORDER_INTAKE"
PURCHASE_ORDER = "PURCHASE_ORDER"
OI_DEBOOKING = "OI_DEBOOKING"
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


def register_all_entities() -> None:
    """Register every entity descriptor, source binding and cross-entity fallback match."""
    if entity_registry.is_registered(ORDER_INTAKE):
        return  # already bootstrapped

    entity_registry.register_entity(_order_intake())
    entity_registry.register_entity(_purchase_order())
    entity_registry.register_entity(_oi_debooking())

    entity_registry.bind_source("SAP_ECC", ORDER_INTAKE, is_primary=True)
    entity_registry.bind_source("SAP_ECC", OI_DEBOOKING, is_primary=False)
    entity_registry.bind_source("RED_BOX_PO", PURCHASE_ORDER, is_primary=True)

    # An order's po_reference resolves against a purchase order's own po_id.
    entity_registry.register_fallback_match(
        ORDER_INTAKE, PURCHASE_ORDER, left_field="po_reference", right_field="po_id"
    )
    # A debooking's order_id resolves against the original order intake's order_id.
    entity_registry.register_fallback_match(
        OI_DEBOOKING, ORDER_INTAKE, left_field="order_id", right_field="order_id"
    )


#: Maps a catalog data source code to the entity it is expected to expose. Used by the
#: seeder to materialise ``data_source_entities`` rows consistent with the registry.
DEFAULT_SOURCE_BINDINGS = {
    "SAP_ECC": (ORDER_INTAKE, True),
    "RED_BOX_PO": (PURCHASE_ORDER, True),
}

#: Additional (non-primary) bindings per source.
ADDITIONAL_SOURCE_BINDINGS = {
    "SAP_ECC": [(OI_DEBOOKING, False)],
}

#: Catalog sources that intentionally have no queryable data in this environment.
UNAVAILABLE_SOURCE_CODES = ("SAPIENS", "BLUE_PLANET", "EMS_SHAREPOINT")


register_all_entities()
