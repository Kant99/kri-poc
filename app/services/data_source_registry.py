"""Data Source Capability Registry.

Establishes the single machine-readable source of truth for *what data actually exists and
where it comes from*. A plan may only reference a data source, entity and field that appears
in a manifest generated here (rule R7).

Design notes
------------
* Field names, SQL types and nullability are **derived from SQLAlchemy metadata** at
  registration time, never hand-listed. This is what prevents manifest drift.
* Registering a new physical table requires only: define the model, register an
  ``EntityDescriptor``, and seed a ``data_source_entities`` binding. No change is needed in
  the tool handlers, the plan service, or the UI.
* ``DataSource.is_queryable`` in the catalog is an *operational* fact maintained by the
  seeder. This module never invents availability; it only reports what bindings exist.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Type

from sqlalchemy import inspect as sa_inspect

# Fields never exposed to the interpreter manifest or record projections.
_INTERNAL_COLUMNS = {"id", "created_at"}


class RegistryError(RuntimeError):
    """Raised when an entity or binding is missing or violates the capability contract."""


# --------------------------------------------------------------------------- helpers


def _normalize(text: Optional[str]) -> str:
    """Lowercase and collapse non-alphanumerics to single spaces."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _type_name(column: Any) -> str:
    """Map a SQLAlchemy column type to a stable, prompt-friendly type name."""
    try:
        python_type = column.type.python_type
    except (NotImplementedError, AttributeError):
        return str(column.type).upper()
    return {
        int: "INTEGER",
        float: "FLOAT",
        str: "STRING",
        bool: "BOOLEAN",
    }.get(python_type, getattr(python_type, "__name__", "STRING").upper())


# --------------------------------------------------------------------------- descriptors


@dataclass(frozen=True)
class FieldDescriptor:
    """A single queryable column exposed to the interpreter and record projection."""

    name: str
    type: str
    nullable: bool
    role: str  # id | record_key | date | amount | source | match_key | attribute

    def to_manifest(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "nullable": self.nullable, "role": self.role}


@dataclass(frozen=True)
class FallbackMatch:
    """A secondary left->right field mapping used when the primary match finds nothing.

    Registered per entity *pair*, because it spans two datasets (e.g. an order's
    ``po_reference`` resolving against a purchase order's ``po_id``).
    """

    left_entity: str
    right_entity: str
    left_field: str
    right_field: str

    def to_manifest(self) -> Dict[str, Any]:
        return {
            "left_field": self.left_field,
            "right_field": self.right_field,
        }


@dataclass
class EntityDescriptor:
    """Describes one queryable business entity backed by a single physical table."""

    entity_code: str
    model: Type
    discriminator_column: str
    date_column: str
    amount_column: str
    id_column: str
    record_key_field: str
    match_fields: Tuple[str, ...]
    aliases: Tuple[str, ...]
    population_role: str = "population"  # population | counterparty
    fields: Tuple[FieldDescriptor, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.fields:
            self.fields = self._derive_fields()
        self._validate()

    def _derive_fields(self) -> Tuple[FieldDescriptor, ...]:
        mapper = sa_inspect(self.model)
        roles: Dict[str, Set[str]] = {
            self.id_column: {"id"},
            self.record_key_field: {"record_key"},
            self.date_column: {"date"},
            self.amount_column: {"amount"},
            self.discriminator_column: {"source"},
        }
        for name in self.match_fields:
            roles.setdefault(name, set()).add("match_key")

        derived: List[FieldDescriptor] = []
        for column in mapper.columns:
            if column.name in _INTERNAL_COLUMNS:
                continue
            derived.append(
                FieldDescriptor(
                    name=column.name,
                    type=_type_name(column),
                    nullable=bool(column.nullable),
                    role=",".join(sorted(roles.get(column.name, {"attribute"}))),
                )
            )
        return tuple(derived)

    def _validate(self) -> None:
        available = {f.name for f in self.fields}
        required = {
            "discriminator_column": self.discriminator_column,
            "date_column": self.date_column,
            "amount_column": self.amount_column,
            "id_column": self.id_column,
            "record_key_field": self.record_key_field,
        }
        for label, column in required.items():
            if column not in available:
                raise RegistryError(
                    f"Entity '{self.entity_code}' declares {label}='{column}' which is not a "
                    f"mapped column of {self.model.__name__}. Available: {sorted(available)}"
                )
        for match_field in self.match_fields:
            if match_field not in available:
                raise RegistryError(
                    f"Entity '{self.entity_code}' declares match field '{match_field}' which is "
                    f"not a mapped column of {self.model.__name__}."
                )

    # -- projections ---------------------------------------------------------

    @property
    def column_names(self) -> List[str]:
        return [f.name for f in self.fields]

    def project(self, record: Any) -> Dict[str, Any]:
        """Project a SQLAlchemy model instance (or dict) into a JSON-safe record dict.

        Dates and datetimes are serialised to ISO-8601 so the projection can be persisted
        directly into a JSON column without a custom encoder.
        """
        import datetime as _dt

        def _coerce(value: Any) -> Any:
            if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
                return value.isoformat()
            if isinstance(value, _dt.timedelta):
                return value.total_seconds()
            return value

        if isinstance(record, dict):
            return {k: _coerce(v) for k, v in record.items() if k in set(self.column_names)}
        return {name: _coerce(getattr(record, name)) for name in self.column_names}

    # -- prompt / manifest ---------------------------------------------------

    @property
    def alias_variants(self) -> List[str]:
        """Normalized mention-scan variants, longest first so 'purchase order' beats 'order'."""
        variants = {_normalize(alias) for alias in self.aliases}
        variants.add(_normalize(self.entity_code.replace("_", " ")))
        return sorted((v for v in variants if v), key=len, reverse=True)

    def to_manifest(self, sample_rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "entity_code": self.entity_code,
            "aliases": list(self.aliases),
            "population_role": self.population_role,
            "fields": [f.to_manifest() for f in self.fields],
            "match_fields": list(self.match_fields),
            "date_column": self.date_column,
            "amount_column": self.amount_column,
            "id_column": self.id_column,
            "record_key_field": self.record_key_field,
        }
        if sample_rows:
            payload["sample_rows"] = list(sample_rows)
        return payload


@dataclass(frozen=True)
class SourceBinding:
    """Declares that a catalog data source exposes a specific entity."""

    data_source_code: str
    entity_code: str
    is_primary: bool = True
    is_active: bool = True


# --------------------------------------------------------------------------- registry


class DataSourceRegistry:
    """Holds entity descriptors and source->entity bindings."""

    def __init__(self) -> None:
        self._entities: Dict[str, EntityDescriptor] = {}
        self._bindings: Dict[str, List[SourceBinding]] = {}
        self._fallback_matches: Dict[Tuple[str, str], FallbackMatch] = {}

    # -- registration -------------------------------------------------------

    def register_entity(self, descriptor: EntityDescriptor) -> EntityDescriptor:
        if descriptor.entity_code in self._entities:
            raise RegistryError(f"Entity '{descriptor.entity_code}' is already registered.")
        self._entities[descriptor.entity_code] = descriptor
        return descriptor

    def register_fallback_match(
        self,
        left_entity: str,
        right_entity: str,
        left_field: str,
        right_field: str,
    ) -> FallbackMatch:
        """Register a secondary match path between two entities, validated against both."""
        left = self.get_entity(left_entity)
        right = self.get_entity(right_entity)
        if left_field not in left.column_names:
            raise RegistryError(
                f"Fallback match left_field '{left_field}' is not a column of {left_entity}."
            )
        if right_field not in right.column_names:
            raise RegistryError(
                f"Fallback match right_field '{right_field}' is not a column of {right_entity}."
            )
        fallback = FallbackMatch(
            left_entity=left_entity,
            right_entity=right_entity,
            left_field=left_field,
            right_field=right_field,
        )
        self._fallback_matches[(left_entity, right_entity)] = fallback
        return fallback

    def fallback_match_for(self, left_entity: str, right_entity: str) -> Optional[FallbackMatch]:
        return self._fallback_matches.get((left_entity, right_entity))

    def bind_source(self, data_source_code: str, entity_code: str, is_primary: bool = True) -> SourceBinding:
        if entity_code not in self._entities:
            raise RegistryError(
                f"Cannot bind '{data_source_code}' to unknown entity '{entity_code}'. "
                f"Registered entities: {sorted(self._entities)}"
            )
        code = data_source_code.strip().upper()
        binding = SourceBinding(data_source_code=code, entity_code=entity_code, is_primary=is_primary)
        existing = self._bindings.setdefault(code, [])
        if any(b.entity_code == entity_code for b in existing):
            return next(b for b in existing if b.entity_code == entity_code)
        existing.append(binding)
        return binding

    def reset(self) -> None:
        self._entities.clear()
        self._bindings.clear()
        self._fallback_matches.clear()

    # -- lookups ------------------------------------------------------------

    def is_registered(self, entity_code: str) -> bool:
        return entity_code in self._entities

    def get_entity(self, entity_code: str) -> EntityDescriptor:
        descriptor = self._entities.get(entity_code)
        if descriptor is None:
            raise RegistryError(
                f"Unknown entity '{entity_code}'. Registered entities: {sorted(self._entities)}"
            )
        return descriptor

    def list_entities(self) -> List[EntityDescriptor]:
        return list(self._entities.values())

    def entity_codes(self) -> List[str]:
        return sorted(self._entities)

    def entities_for_source(self, data_source_code: str) -> List[EntityDescriptor]:
        code = (data_source_code or "").strip().upper()
        return [
            self.get_entity(b.entity_code)
            for b in self._bindings.get(code, [])
            if b.is_active and b.entity_code in self._entities
        ]

    def bound_entity_codes(self, data_source_code: str) -> List[str]:
        return [d.entity_code for d in self.entities_for_source(data_source_code)]

    def sources_exposing(self, entity_code: str) -> List[str]:
        return sorted(
            code
            for code, bindings in self._bindings.items()
            if any(b.entity_code == entity_code and b.is_active for b in bindings)
        )

    def source_codes(self) -> List[str]:
        return sorted(self._bindings)

    def match_fields_between(
        self, left_entity: str, right_entity: str
    ) -> List[str]:
        """Return declared match fields present on *both* entities, left entity order preserved."""
        left = self.get_entity(left_entity)
        right = self.get_entity(right_entity)
        right_columns = set(right.column_names)
        return [name for name in left.match_fields if name in right_columns]

    def allowed_source_systems(self) -> Set[str]:
        return set(self._bindings)

    def allowed_entities(self) -> Set[str]:
        return set(self._entities)

    def allowed_matching_fields(self) -> Set[str]:
        fields: Set[str] = set()
        for descriptor in self._entities.values():
            fields.update(descriptor.match_fields)
        return fields


entity_registry = DataSourceRegistry()


# --------------------------------------------------------------------------- manifest


def source_mention_variants(data_source: Any) -> List[str]:
    """Normalized mention-scan variants for a catalog ``DataSource`` row.

    Includes the code, the human name, punctuation-stripped forms and a distinctive first
    token, so "SAP ECC", "sap_ecc" and "SAP" all resolve to the same source. Sorted longest
    first so more specific names win.
    """
    variants: Set[str] = set()
    code = getattr(data_source, "code", None)
    name = getattr(data_source, "name", None)

    for raw in (code, name):
        normalized = _normalize(raw)
        if normalized:
            variants.add(normalized)
            variants.add(normalized.replace(" ", "_"))

    # First distinctive token, e.g. "SAP ECC" -> "sap", "Red Box / PO" -> "red".
    # Guard against overly generic single tokens.
    generic = {"the", "and", "po", "system", "core", "data", "hub", "box", "blue", "ems"}
    for token in _normalize(name).split():
        if len(token) >= 3 and token not in generic:
            variants.add(token)
    for token in _normalize(code).split():
        if len(token) >= 3 and token not in generic:
            variants.add(token)

    return sorted((v for v in variants if v), key=len, reverse=True)


def resolve_bindings(db: Any = None) -> Dict[str, List[str]]:
    """Return data source code -> exposed entity codes.

    The ``data_source_entities`` table is authoritative when a session is available, because
    an administrator can add or remove a binding. The in-memory registry supplies the
    default wiring when no session is available.
    """
    if db is not None:
        try:
            from app.models.kri import DataSource, DataSourceEntity

            by_id = {ds.id: ds.code.upper() for ds in db.query(DataSource).all()}
            bindings: Dict[str, List[str]] = {}
            for row in (
                db.query(DataSourceEntity).filter(DataSourceEntity.is_active == True).all()
            ):
                code = by_id.get(row.data_source_id)
                if code:
                    bindings.setdefault(code, []).append(row.entity_code)
            return bindings
        except Exception:  # pragma: no cover - fall back to registry defaults
            pass
    return {
        code: entity_registry.bound_entity_codes(code) for code in entity_registry.source_codes()
    }


def build_capability_manifest(
    kri: Any,
    db: Any = None,
    sample_rows: int = 0,
    db_bindings: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Build the capability manifest for one KRI.

    The manifest is the *only* enumeration of legal data sources, entities and fields handed
    to the plan interpreter (R7). It is built exclusively from the KRI's assigned
    ``DataSource`` rows intersected with the registered bindings.
    """
    assigned = list(getattr(kri, "data_sources", None) or [])
    assigned_codes = {ds.code.upper() for ds in assigned}
    bindings = db_bindings if db_bindings is not None else resolve_bindings(db)

    data_sources: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    queryable_codes: Set[str] = set()

    for ds in assigned:
        descriptors = [
            entity_registry.get_entity(code)
            for code in bindings.get(ds.code.upper(), [])
            if entity_registry.is_registered(code)
        ]
        entry: Dict[str, Any] = {
            "code": ds.code,
            "name": ds.name,
            "system_type": ds.system_type,
            "description": ds.description,
            "is_queryable": bool(descriptors),
            "aliases": source_mention_variants(ds),
        }
        if not descriptors:
            unavailable.append(entry)
            continue
        queryable_codes.add(ds.code.upper())
        entry["is_queryable"] = True
        entry["entities"] = [
            descriptor.to_manifest(
                sample_rows=_sample_for(db, descriptor, ds.code, sample_rows) if sample_rows else None
            )
            for descriptor in descriptors
        ]
        data_sources.append(entry)

    # Assigned-but-unbound sources are reported so the interpreter can never read them (R8).
    unbound_assigned = [
        {
            "code": ds.code,
            "name": ds.name,
            "reason": "assigned to this KRI but exposes no queryable entity in this environment",
        }
        for ds in assigned
        if ds.code.upper() not in queryable_codes
    ]

    # Entities reachable by this KRI, used to bound MATCH/COMPARE field resolution.
    reachable_entities = sorted(
        {
            entity_code
            for ds in assigned
            for entity_code in bindings.get(ds.code.upper(), [])
            if entity_registry.is_registered(entity_code)
        }
    )
    entity_pairs: List[Dict[str, Any]] = []
    for i, left in enumerate(reachable_entities):
        for right in reachable_entities[i + 1 :]:
            fallback = entity_registry.fallback_match_for(left, right)
            entry: Dict[str, Any] = {
                "left_entity": left,
                "right_entity": right,
                "shared_match_fields": entity_registry.match_fields_between(left, right),
            }
            if fallback is None:
                fallback = entity_registry.fallback_match_for(right, left)
                if fallback is not None:
                    entry["fallback_match"] = {
                        "left_field": fallback.right_field,
                        "right_field": fallback.left_field,
                    }
            else:
                entry["fallback_match"] = fallback.to_manifest()
            entity_pairs.append(entry)

    return {
        "kri_id": getattr(kri, "id", None),
        "kri_identifier": getattr(kri, "identifier", None),
        "data_sources": data_sources,
        "unavailable_data_sources": unavailable,
        "unbound_assigned_sources": unbound_assigned,
        "reachable_entities": reachable_entities,
        "entity_pairs": entity_pairs,
    }


def _sample_for(
    db: Any, descriptor: EntityDescriptor, data_source_code: str, limit: int
) -> List[Dict[str, Any]]:
    """Fetch a small masked sample of rows so the LLM can map business words to columns."""
    if db is None or limit <= 0:
        return []
    from app.core.security import mask_sensitive_dict

    try:
        model = descriptor.model
        query = db.query(model)
        if descriptor.discriminator_column:
            query = query.filter(
                getattr(model, descriptor.discriminator_column) == data_source_code
            )
        rows = query.limit(limit).all()
        return [mask_sensitive_dict(descriptor.project(row)) for row in rows]
    except Exception:  # pragma: no cover - sampling is advisory only
        return []


def iter_manifest_entity_codes(manifest: Dict[str, Any]) -> Iterable[str]:
    for source in manifest.get("data_sources", []):
        for entity in source.get("entities", []):
            yield entity["entity_code"]
