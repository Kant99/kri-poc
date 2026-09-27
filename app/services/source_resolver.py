"""Deterministic Data Source Resolver.

Resolves *which data source and entity* an extract step must read, given:

1. the KRI's assigned ``DataSource`` rows,
2. the capability registry bindings,
3. the natural language text of the step,
4. any explicit pin on the step,
5. an optional LLM proposal (used only as corroboration / tie-breaker).

The resolver never guesses. Every ambiguous or unresolvable case raises
``SourceResolutionError`` naming exactly what the user must clarify or configure
(rules R4 and R8).
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.data_source_registry import (
    DataSourceRegistry,
    EntityDescriptor,
    entity_registry,
    source_mention_variants,
    _normalize,
)


class SourceResolutionError(ValueError):
    """A step's data source could not be resolved unambiguously."""

    def __init__(self, message: str, candidates: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.candidates: List[str] = list(candidates or [])

    def to_dict(self) -> Dict[str, Any]:
        return {"message": str(self), "candidates": self.candidates}


@dataclass
class SourceResolution:
    """The resolved, validated read target for one extract step."""

    data_source_id: int
    data_source_code: str
    entity_code: str
    selector_reason: str
    origin: str  # PINNED | LLM | RULES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_source_id": self.data_source_id,
            "data_source_code": self.data_source_code,
            "entity_code": self.entity_code,
            "selector_reason": self.selector_reason,
            "origin": self.origin,
        }


@dataclass
class Mention:
    """A matched source or entity phrase found in step text."""

    text: str
    payload: Any
    kind: str  # source | entity


class DataSourceResolver:
    """Resolves extract-step read targets against assigned sources and the registry.

    ``db_bindings`` overrides the registry's default wiring with the authoritative
    ``data_source_entities`` rows, so an administrator adding or removing a binding changes
    what a plan is allowed to read.
    """

    def __init__(
        self,
        registry: Optional[DataSourceRegistry] = None,
        db_bindings: Optional[Dict[str, Sequence[str]]] = None,
    ):
        self.registry = registry or entity_registry
        self.db_bindings = db_bindings
        self.unassigned_mentions: List[Mention] = []

    # -- binding lookups ------------------------------------------------------

    def bound_entity_codes(self, data_source_code: str) -> List[str]:
        """Entities exposed by a source, preferring the database bindings over defaults."""
        code = (data_source_code or "").strip().upper()
        if self.db_bindings is not None:
            return [e for e in self.db_bindings.get(code, []) if self.registry.is_registered(e)]
        return self.registry.bound_entity_codes(code)

    def sources_exposing(self, entity_code: str) -> List[str]:
        """Catalog codes that expose an entity, from the authoritative bindings."""
        code = (entity_code or "").strip().upper()
        if self.db_bindings is not None:
            return sorted(src for src, entities in self.db_bindings.items() if code in entities)
        return self.registry.sources_exposing(code)

    # -- mention scanning ----------------------------------------------------

    def scan_text(
        self,
        text: str,
        assigned_sources: Sequence[Any],
        catalog_sources: Optional[Sequence[Any]] = None,
    ) -> Tuple[List[Mention], List[Mention]]:
        """Return ``(source_mentions, entity_mentions)`` found in ``text``, longest match wins.

        Source mentions cover the *whole catalog*, not just assigned sources, so a step that
        names an unassigned source is detected and reported rather than silently ignored.
        ``self.unassigned_mentions`` holds the non-assigned hits for the caller to raise on.
        """
        normalized = f" {_normalize(text)} "
        assigned_codes = {ds.code.upper() for ds in assigned_sources}
        pool = list(catalog_sources or assigned_sources)

        source_hits: List[Mention] = []
        for ds in pool:
            for variant in source_mention_variants(ds):
                if variant and f" {variant} " in normalized:
                    source_hits.append(Mention(text=variant, payload=ds, kind="source"))
                    break  # longest variant per source wins
        source_hits.sort(key=lambda m: len(m.text), reverse=True)

        entity_hits: List[Mention] = []
        for descriptor in self.registry.list_entities():
            for variant in descriptor.alias_variants:
                if variant and f" {variant} " in normalized:
                    entity_hits.append(Mention(text=variant, payload=descriptor, kind="entity"))
                    break
        entity_hits.sort(key=lambda m: len(m.text), reverse=True)

        # Overlapping entity names are normal - "order intake debookings" contains "order
        # intake". The most specific name that actually occurs is what the author meant, so
        # keep only the entities whose longest matching name is the longest overall. Shorter
        # nested hits are absorbed rather than reported as a second, conflicting entity.
        if entity_hits:
            longest = len(entity_hits[0].text)
            specific = [m for m in entity_hits if len(m.text) == longest]
            kept: List[Mention] = []
            for mention in specific:
                if all(mention.payload.entity_code != seen.payload.entity_code for seen in kept):
                    kept.append(mention)
            entity_hits = kept

        self.unassigned_mentions = [
            m for m in source_hits if m.payload.code.upper() not in assigned_codes
        ]
        return [m for m in source_hits if m.payload.code.upper() in assigned_codes], entity_hits

    # -- public entry point --------------------------------------------------

    def resolve(
        self,
        kri: Any,
        step_text: str,
        catalog_sources: Optional[Sequence[Any]] = None,
        pinned_source_id: Optional[int] = None,
        pinned_entity_code: Optional[str] = None,
        proposed_source_code: Optional[str] = None,
        proposed_entity_code: Optional[str] = None,
    ) -> SourceResolution:
        """Resolve the read target for one extract step, or raise ``SourceResolutionError``."""
        assigned = list(getattr(kri, "data_sources", None) or [])
        by_id = {ds.id: ds for ds in assigned}
        by_code = {ds.code.upper(): ds for ds in assigned}

        # -- 1. Explicit pin wins, but must still be legal. --------------------
        if pinned_source_id is not None or pinned_entity_code is not None:
            if pinned_source_id is None:
                raise SourceResolutionError(
                    "Step pins an entity_code but no data_source_id. Pin the data source as well.",
                )
            if pinned_source_id not in by_id:
                assigned_codes = sorted(by_code)
                raise SourceResolutionError(
                    f"Step pins data source id {pinned_source_id}, which is not assigned to this "
                    f"KRI. Assigned data sources: {assigned_codes or 'none'}.",
                    candidates=assigned_codes,
                )
            ds = by_id[pinned_source_id]
            if pinned_entity_code is not None:
                if not self.registry.is_registered(pinned_entity_code):
                    raise SourceResolutionError(
                        f"Step pins unknown entity '{pinned_entity_code}'. "
                        f"Registered entities: {self.registry.entity_codes()}.",
                        candidates=self.registry.entity_codes(),
                    )
                if pinned_entity_code not in self.bound_entity_codes(ds.code):
                    bound = self.bound_entity_codes(ds.code)
                    raise SourceResolutionError(
                        f"Data source '{ds.code}' does not expose entity "
                        f"'{pinned_entity_code}'. Exposed entities: {bound or 'none'}.",
                        candidates=bound,
                    )
                entity_code = pinned_entity_code
            else:
                entity_code = self._sole_bound_entity(ds)
            return SourceResolution(
                data_source_id=ds.id,
                data_source_code=ds.code,
                entity_code=entity_code,
                selector_reason=f"pinned on step (source={ds.code}, entity={entity_code})",
                origin="PINNED",
            )

        # -- 2. Mention scan over step text. -----------------------------------
        catalog = list(catalog_sources or assigned)
        source_mentions, entity_mentions = self.scan_text(step_text, assigned, catalog_sources=catalog)
        unassigned = list(self.unassigned_mentions)

        # An explicitly named but unassigned source is a hard configuration error (R8).
        if unassigned:
            codes = sorted({m.payload.code.upper() for m in unassigned})
            raise SourceResolutionError(
                f"Step references data source(s) {codes}, which are not assigned to this KRI. "
                f"Assign them in the KRI configuration or change the step text. "
                f"Assigned data sources: {sorted(by_code) or 'none'}.",
                candidates=sorted(by_code),
            )

        named_source = source_mentions[0].payload if source_mentions else None
        named_entity = entity_mentions[0].payload if entity_mentions else None

        # -- 3. Source named. --------------------------------------------------
        if named_source is not None:
            bound = self.bound_entity_codes(named_source.code)
            if not bound:
                raise SourceResolutionError(
                    f"Data source '{named_source.code}' is assigned to this KRI but exposes no "
                    f"queryable entity in this environment, so no data can be read from it. "
                    f"Remove it from the KRI or add an entity binding.",
                    candidates=[],
                )
            if named_entity is not None:
                if named_entity.entity_code not in bound:
                    raise SourceResolutionError(
                        f"Step mentions {named_entity.entity_code} but data source "
                        f"'{named_source.code}' does not expose it. Exposed entities: {bound}.",
                        candidates=bound,
                    )
                return SourceResolution(
                    data_source_id=named_source.id,
                    data_source_code=named_source.code,
                    entity_code=named_entity.entity_code,
                    selector_reason=(
                        f"source '{named_source.code}' and entity "
                        f"'{named_entity.entity_code}' both named in step text"
                    ),
                    origin="RULES",
                )
            if len(bound) == 1:
                return SourceResolution(
                    data_source_id=named_source.id,
                    data_source_code=named_source.code,
                    entity_code=bound[0],
                    selector_reason=(
                        f"source '{named_source.code}' named in step text; it exposes exactly "
                        f"one entity ({bound[0]})"
                    ),
                    origin="RULES",
                )
            raise SourceResolutionError(
                f"Step names data source '{named_source.code}', which exposes multiple entities "
                f"{bound}. Name the entity explicitly in the step to disambiguate.",
                candidates=bound,
            )

        # -- 4. Entity named, no source. ---------------------------------------
        if named_entity is not None:
            code = named_entity.entity_code
            candidates = [c for c in self.sources_exposing(code) if c in by_code]
            if not candidates:
                raise SourceResolutionError(
                    f"Step mentions entity '{code}', but no data source assigned to this KRI "
                    f"exposes it. Assigned sources expose: "
                    f"{self._assigned_entity_summary(by_code)}.",
                    candidates=sorted(by_code),
                )
            if len(candidates) == 1:
                ds = by_code[candidates[0]]
                return SourceResolution(
                    data_source_id=ds.id,
                    data_source_code=ds.code,
                    entity_code=code,
                    selector_reason=(
                        f"entity '{code}' named in step text; only assigned source "
                        f"'{ds.code}' exposes it"
                    ),
                    origin="RULES",
                )
            raise SourceResolutionError(
                f"Step mentions entity '{code}', which is exposed by multiple assigned data "
                f"sources {candidates}. Name the data source explicitly to disambiguate.",
                candidates=candidates,
            )

        # -- 5. Nothing named: fall back to the LLM proposal, then uniqueness. --
        if proposed_source_code and proposed_entity_code:
            code = proposed_source_code.upper()
            if code not in by_code:
                raise SourceResolutionError(
                    f"Proposed data source '{code}' is not assigned to this KRI. "
                    f"Assigned: {sorted(by_code) or 'none'}.",
                    candidates=sorted(by_code),
                )
            bound = self.bound_entity_codes(code)
            if proposed_entity_code not in bound:
                raise SourceResolutionError(
                    f"Proposed entity '{proposed_entity_code}' is not exposed by data source "
                    f"'{code}'. Exposed entities: {bound or 'none'}.",
                    candidates=bound,
                )
            return SourceResolution(
                data_source_id=by_code[code].id,
                data_source_code=code,
                entity_code=proposed_entity_code,
                selector_reason=(
                    f"selected from the capability manifest by the interpreter "
                    f"(source={code}, entity={proposed_entity_code})"
                ),
                origin="LLM",
            )

        all_bindings = [
            (ds.code.upper(), entity)
            for ds in assigned
            for entity in self.bound_entity_codes(ds.code)
        ]
        if len(all_bindings) == 1:
            code, entity = all_bindings[0]
            return SourceResolution(
                data_source_id=by_code[code].id,
                data_source_code=code,
                entity_code=entity,
                selector_reason=(
                    f"step text names no data source; '{code}' is the only assigned source "
                    f"exposing a queryable entity"
                ),
                origin="RULES",
            )
        raise SourceResolutionError(
            "Step does not identify which data source to read. Name the data source "
            "(e.g. 'from SAP ECC') or pin one on the step. "
            f"Assigned sources expose: {self._assigned_entity_summary(by_code)}.",
            candidates=sorted({code for code, _ in all_bindings}),
        )

    # -- helpers -------------------------------------------------------------

    def _reindex(self, sources: Sequence[Any]):
        by_id = {ds.id: ds for ds in sources}
        by_code = {ds.code.upper(): ds for ds in sources}
        return list(sources), by_id, by_code

    def _sole_bound_entity(self, ds: Any) -> str:
        bound = self.bound_entity_codes(ds.code)
        if not bound:
            raise SourceResolutionError(
                f"Data source '{ds.code}' is assigned to this KRI but exposes no queryable "
                f"entity in this environment, so no data can be read from it.",
                candidates=[],
            )
        if len(bound) > 1:
            raise SourceResolutionError(
                f"Data source '{ds.code}' exposes multiple entities {bound}. Pin entity_code "
                f"on the step to disambiguate.",
                candidates=bound,
            )
        return bound[0]

    def _assigned_entity_summary(self, by_code: Dict[str, Any]) -> str:
        if not by_code:
            return "none"
        parts = [
            f"{code} -> {self.bound_entity_codes(code) or 'no queryable entity'}"
            for code in sorted(by_code)
        ]
        return "; ".join(parts)
