"""Repository for KRI Configuration, Steps, Thresholds, Schedules, and Data Sources."""

import json
from datetime import datetime
from typing import List, Optional, Sequence, Tuple
from sqlalchemy.orm import Session, joinedload
from app.models.kri import (
    KRI,
    ProcessArea,
    DataSource,
    DataSourceEntity,
    KRITestStep,
    KRIThreshold,
    KRISchedule,
    Reviewer,
)
from app.schemas.kri import (
    KRICreate,
    KRIUpdate,
    KRITestStepCreate,
    KRITestStepUpdate,
    StepReorderItem,
    KRIThresholdCreate,
    KRIThresholdUpdate,
    KRIScheduleCreate,
    ReviewerCreate,
)


class KRIRepository:
    """Handles persistence operations for KRI entities."""

    def __init__(self, db: Session):
        self.db = db

    # Process Area Operations
    def get_or_create_process_area(self, name: str, code: str, description: Optional[str] = None) -> ProcessArea:
        pa = self.db.query(ProcessArea).filter(ProcessArea.code == code).first()
        if not pa:
            pa = ProcessArea(name=name, code=code, description=description)
            self.db.add(pa)
            self.db.commit()
            self.db.refresh(pa)
        return pa

    def list_process_areas(self) -> List[ProcessArea]:
        return self.db.query(ProcessArea).all()

    # Data Source Operations
    def get_or_create_data_source(
        self,
        name: str,
        code: str,
        system_type: str,
        description: Optional[str] = None,
        is_queryable: Optional[bool] = None,
        availability_note: Optional[str] = None,
    ) -> DataSource:
        ds = self.db.query(DataSource).filter(DataSource.code == code).first()
        if not ds:
            ds = DataSource(
                name=name,
                code=code,
                system_type=system_type,
                description=description,
                is_queryable=bool(is_queryable),
                availability_note=availability_note,
            )
            self.db.add(ds)
            self.db.commit()
            self.db.refresh(ds)
        return ds

    def list_data_sources(self) -> List[DataSource]:
        return (
            self.db.query(DataSource)
            .options(joinedload(DataSource.entity_bindings))
            .order_by(DataSource.id)
            .all()
        )

    def get_data_source(self, data_source_id: int) -> Optional[DataSource]:
        return self.db.query(DataSource).filter(DataSource.id == data_source_id).first()

    def set_entity_bindings(
        self, data_source_id: int, bindings: Sequence[Tuple[str, bool]]
    ) -> DataSource:
        """Replace the entity bindings for a data source.

        Availability is derived from the bindings: a source with no bindings is not
        queryable, regardless of the stored flag.
        """
        ds = self.get_data_source(data_source_id)
        if not ds:
            raise ValueError(f"Data source {data_source_id} not found.")

        ds.entity_bindings.clear()
        for entity_code, is_primary in bindings:
            ds.entity_bindings.append(
                DataSourceEntity(
                    data_source_id=ds.id,
                    entity_code=entity_code,
                    is_primary=is_primary,
                    is_active=True,
                )
            )
        ds.is_queryable = bool(bindings)
        if not bindings and not ds.availability_note:
            ds.availability_note = "No queryable entity is bound to this data source."
        self.db.commit()
        self.db.refresh(ds)
        return ds

    def entity_codes_for_source(self, data_source_id: int) -> List[str]:
        return [
            binding.entity_code
            for binding in self.db.query(DataSourceEntity)
            .filter(DataSourceEntity.data_source_id == data_source_id, DataSourceEntity.is_active == True)
            .all()
        ]

    def bindings_by_code(self) -> dict:
        """Map data source code -> exposed entity codes, straight from the DB bindings.

        This is the authoritative binding source: the in-memory registry declares the
        *default* wiring, but an administrator can add or remove a binding, and a plan must
        reflect the database, not a hardcoded default.
        """
        by_id = {ds.id: ds.code.upper() for ds in self.db.query(DataSource).all()}
        result: dict = {}
        for binding in self.db.query(DataSourceEntity).filter(DataSourceEntity.is_active == True).all():
            code = by_id.get(binding.data_source_id)
            if not code:
                continue
            result.setdefault(code, []).append(binding.entity_code)
        return result

    def sync_data_source_availability(self) -> None:
        """Reconcile ``is_queryable`` with the actual entity bindings (R8)."""
        for ds in self.db.query(DataSource).options(joinedload(DataSource.entity_bindings)).all():
            queryable = any(b.is_active for b in ds.entity_bindings)
            if bool(ds.is_queryable) != queryable:
                ds.is_queryable = queryable
                if not queryable and not ds.availability_note:
                    ds.availability_note = "No queryable entity is bound to this data source."
        self.db.commit()

    # KRI Operations
    def create_kri(self, kri_in: KRICreate) -> KRI:
        kri = KRI(
            identifier=kri_in.identifier,
            name=kri_in.name,
            process_area_id=kri_in.process_area_id,
            indicator_type=kri_in.indicator_type.value,
            risk_description=kri_in.risk_description,
            end_goal=kri_in.end_goal,
            owner=kri_in.owner,
            note=kri_in.note,
            status="DRAFT",
        )
        if kri_in.data_source_ids:
            sources = self.db.query(DataSource).filter(DataSource.id.in_(kri_in.data_source_ids)).all()
            kri.data_sources.extend(sources)

        self.db.add(kri)
        self.db.commit()
        self.db.refresh(kri)
        return self.get_kri_by_id(kri.id)

    def get_kri_by_id(self, kri_id: int) -> Optional[KRI]:
        return (
            self.db.query(KRI)
            .options(
                joinedload(KRI.process_area),
                joinedload(KRI.data_sources),
                joinedload(KRI.test_steps),
                joinedload(KRI.thresholds),
                joinedload(KRI.schedules),
                joinedload(KRI.reviewers),
            )
            .filter(KRI.id == kri_id)
            .first()
        )

    def set_data_sources(self, kri_id: int, data_source_ids: Sequence[int]) -> KRI:
        """Replace the KRI's assigned data sources.

        Because the assignment participates in ``steps_hash``, changing it invalidates the
        execution plan exactly as editing step text does (R3).
        """
        kri = self.get_kri_by_id(kri_id)
        if not kri:
            return None
        sources = self.db.query(DataSource).filter(DataSource.id.in_(list(data_source_ids))).all()
        kri.data_sources = sources
        kri.updated_at = datetime.utcnow()
        self.db.commit()
        return self.get_kri_by_id(kri_id)

    def list_all_data_sources(self) -> List[DataSource]:
        """Whole catalog, including sources a given KRI does not use.

        The plan interpreter needs this so a step naming an *unassigned* source is detected
        and reported, rather than silently resolving to something else.
        """
        return (
            self.db.query(DataSource)
            .options(joinedload(DataSource.entity_bindings))
            .order_by(DataSource.id)
            .all()
        )

    def get_kri_by_identifier(self, identifier: str) -> Optional[KRI]:
        return (
            self.db.query(KRI)
            .options(
                joinedload(KRI.process_area),
                joinedload(KRI.data_sources),
                joinedload(KRI.test_steps),
                joinedload(KRI.thresholds),
                joinedload(KRI.schedules),
                joinedload(KRI.reviewers),
            )
            .filter(KRI.identifier == identifier)
            .first()
        )

    def list_kris(self, status: Optional[str] = None, process_area_id: Optional[int] = None) -> List[KRI]:
        query = self.db.query(KRI).options(
            joinedload(KRI.process_area),
            joinedload(KRI.data_sources),
            joinedload(KRI.test_steps),
            joinedload(KRI.thresholds),
            joinedload(KRI.schedules),
            joinedload(KRI.reviewers),
        )
        if status:
            query = query.filter(KRI.status == status)
        if process_area_id:
            query = query.filter(KRI.process_area_id == process_area_id)
        return query.all()

    def update_kri(self, kri_id: int, kri_in: KRIUpdate) -> Optional[KRI]:
        kri = self.get_kri_by_id(kri_id)
        if not kri:
            return None

        if kri_in.name is not None:
            kri.name = kri_in.name
        if kri_in.process_area_id is not None:
            kri.process_area_id = kri_in.process_area_id
        if kri_in.indicator_type is not None:
            kri.indicator_type = kri_in.indicator_type.value
        if kri_in.risk_description is not None:
            kri.risk_description = kri_in.risk_description
        if kri_in.end_goal is not None:
            kri.end_goal = kri_in.end_goal
        if kri_in.owner is not None:
            kri.owner = kri_in.owner
        if kri_in.note is not None:
            kri.note = kri_in.note

        if kri_in.data_source_ids is not None:
            sources = self.db.query(DataSource).filter(DataSource.id.in_(kri_in.data_source_ids)).all()
            kri.data_sources = sources

        kri.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(kri)
        return self.get_kri_by_id(kri_id)

    def update_status(self, kri_id: int, status: str) -> Optional[KRI]:
        kri = self.get_kri_by_id(kri_id)
        if not kri:
            return None
        kri.status = status
        kri.updated_at = datetime.utcnow()
        if status == "ACTIVE":
            kri.validated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(kri)
        return kri

    # Test Step Operations
    def _apply_step_input(self, step: KRITestStep, step_in) -> None:
        """Copy a step create/update payload onto the row and refresh its content hash."""
        if step_in.title is not None:
            step.title = step_in.title
        if step_in.instruction is not None:
            step.instruction = step_in.instruction
        if getattr(step_in, "is_active", None) is not None:
            step.is_active = step_in.is_active
        if getattr(step_in, "step_number", None) is not None:
            step.step_number = step_in.step_number
        if getattr(step_in, "operation", None) is not None:
            step.operation = step_in.operation.value if hasattr(step_in.operation, "value") else step_in.operation
        if getattr(step_in, "parameters", None) is not None:
            step.parameters = step_in.parameters
        if getattr(step_in, "data_source_id", "sentinel") != "sentinel":
            step.data_source_id = step_in.data_source_id
        if getattr(step_in, "entity_code", None) is not None:
            step.entity_code = step_in.entity_code
        if getattr(step_in, "expected_output", None) is not None:
            step.expected_output = step_in.expected_output
        step.updated_at = datetime.utcnow()

    def _refresh_hashes(self, kri_id: int) -> None:
        for step in self.db.query(KRITestStep).filter(KRITestStep.kri_id == kri_id).all():
            step.content_hash = step.compute_content_hash()

    def add_test_step(self, kri_id: int, step_in: KRITestStepCreate) -> KRITestStep:
        """Append or insert a step, then renumber so the sequence stays contiguous.

        The requested ``step_number`` is honoured as a position, so posting a step numbered 4
        when only 1 and 2 exist slots it in third place rather than leaving a gap. Gaps would
        otherwise make the interpreted plan fail its contiguity check.
        """
        step = KRITestStep(
            kri_id=kri_id,
            step_number=step_in.step_number,
            title=step_in.title,
            instruction=step_in.instruction,
            is_active=step_in.is_active,
            operation=step_in.operation.value if step_in.operation else None,
            parameters=step_in.parameters,
            data_source_id=step_in.data_source_id,
            entity_code=step_in.entity_code,
            expected_output=step_in.expected_output,
        )
        self.db.add(step)
        self.db.commit()
        self.renumber_test_steps(kri_id)
        self.db.refresh(step)
        return step

    def update_test_step(self, step_id: int, step_in: KRITestStepUpdate) -> Optional[KRITestStep]:
        step = self.db.query(KRITestStep).filter(KRITestStep.id == step_id).first()
        if not step:
            return None
        self._apply_step_input(step, step_in)
        self.db.commit()
        self.renumber_test_steps(step.kri_id)
        self.db.refresh(step)
        return step

    def delete_test_step(self, step_id: int) -> bool:
        step = self.db.query(KRITestStep).filter(KRITestStep.id == step_id).first()
        if not step:
            return False
        kri_id = step.kri_id
        self.db.delete(step)
        self.db.commit()
        self._refresh_hashes(kri_id)
        self.db.commit()
        return True

    def replace_test_steps(self, kri_id: int, steps: Sequence[KRITestStepCreate]) -> List[KRITestStep]:
        """Atomically replace the whole ordered step set, then refresh every content hash."""
        self.db.query(KRITestStep).filter(KRITestStep.kri_id == kri_id).delete(
            synchronize_session="fetch"
        )
        # A bulk delete leaves already-loaded relationship collections stale, so drop the
        # identity map; otherwise the caller reads back the pre-delete step set.
        self.db.expire_all()
        for index, step_in in enumerate(steps, start=1):
            self.db.add(
                KRITestStep(
                    kri_id=kri_id,
                    step_number=step_in.step_number or index,
                    title=step_in.title,
                    instruction=step_in.instruction,
                    is_active=step_in.is_active,
                    operation=step_in.operation.value if step_in.operation else None,
                    parameters=step_in.parameters,
                    data_source_id=step_in.data_source_id,
                    entity_code=step_in.entity_code,
                    expected_output=step_in.expected_output,
                )
            )
        self.db.commit()
        self._refresh_hashes(kri_id)
        self.db.commit()
        return self.get_kri_by_id(kri_id).test_steps

    def reorder_test_steps(self, kri_id: int, reorder_list: List[StepReorderItem]) -> List[KRITestStep]:
        for item in reorder_list:
            step = self.db.query(KRITestStep).filter(KRITestStep.id == item.step_id, KRITestStep.kri_id == kri_id).first()
            if step:
                step.step_number = item.new_step_number
                step.updated_at = datetime.utcnow()
        self.db.commit()
        return self.renumber_test_steps(kri_id)

    def renumber_test_steps(self, kri_id: int) -> List[KRITestStep]:
        """Force step numbers to be contiguous from 1, preserving the current order.

        Ordering is by ``(step_number, id)`` so a newly inserted step lands in the position
        the caller asked for, and ties resolve in insertion order.
        """
        steps = (
            self.db.query(KRITestStep)
            .filter(KRITestStep.kri_id == kri_id)
            .order_by(KRITestStep.step_number, KRITestStep.id)
            .all()
        )
        for index, step in enumerate(steps, start=1):
            if step.step_number != index:
                step.step_number = index
                step.updated_at = datetime.utcnow()
        self.db.commit()
        self._refresh_hashes(kri_id)
        self.db.commit()
        return steps

    # -- snapshot / restore ---------------------------------------------------

    def snapshot_steps(self, kri_id: int) -> List[Tuple]:
        """Capture the full step set so a rejected write can be rolled back exactly.

        A step write that cannot be interpreted must leave nothing behind (R3): the stored
        steps and the stored plan can never disagree.
        """
        return [
            (
                step.id,
                step.step_number,
                step.title,
                step.instruction,
                step.is_active,
                step.operation,
                json.dumps(step.parameters, sort_keys=True) if step.parameters else None,
                step.data_source_id,
                step.entity_code,
                step.expected_output,
            )
            for step in (
                self.db.query(KRITestStep)
                .filter(KRITestStep.kri_id == kri_id)
                .order_by(KRITestStep.step_number)
                .all()
            )
        ]

    def restore_steps(self, kri_id: int, snapshot: Sequence[Tuple]) -> None:
        """Restore a step snapshot taken by :meth:`snapshot_steps`."""
        self.db.query(KRITestStep).filter(KRITestStep.kri_id == kri_id).delete(
            synchronize_session="fetch"
        )
        self.db.expire_all()
        for row in snapshot:
            (
                step_id, step_number, title, instruction, is_active, operation,
                parameters, data_source_id, entity_code, expected_output,
            ) = row
            self.db.add(
                KRITestStep(
                    id=step_id,
                    kri_id=kri_id,
                    step_number=step_number,
                    title=title,
                    instruction=instruction,
                    is_active=is_active,
                    operation=operation,
                    parameters=json.loads(parameters) if parameters else None,
                    data_source_id=data_source_id,
                    entity_code=entity_code,
                    expected_output=expected_output,
                )
            )
        self.db.commit()
        self._refresh_hashes(kri_id)
        self.db.commit()

    # Threshold Operations
    def add_threshold(self, kri_id: int, threshold_in: KRIThresholdCreate) -> KRIThreshold:
        th = KRIThreshold(
            kri_id=kri_id,
            name=threshold_in.name,
            threshold_type=threshold_in.threshold_type.value,
            operator=threshold_in.operator.value,
            threshold_value=threshold_in.threshold_value,
            key=threshold_in.key,
            unit=threshold_in.unit,
            is_active=threshold_in.is_active,
        )
        self.db.add(th)
        self.db.commit()
        self.db.refresh(th)
        return th

    def update_threshold(self, threshold_id: int, threshold_in: KRIThresholdUpdate) -> Optional[KRIThreshold]:
        th = self.db.query(KRIThreshold).filter(KRIThreshold.id == threshold_id).first()
        if not th:
            return None
        if threshold_in.name is not None:
            th.name = threshold_in.name
        if threshold_in.threshold_type is not None:
            th.threshold_type = threshold_in.threshold_type.value
        if threshold_in.operator is not None:
            th.operator = threshold_in.operator.value
        if threshold_in.threshold_value is not None:
            th.threshold_value = threshold_in.threshold_value
        if threshold_in.key is not None:
            th.key = threshold_in.key
        if threshold_in.unit is not None:
            th.unit = threshold_in.unit
        if threshold_in.is_active is not None:
            th.is_active = threshold_in.is_active
        th.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(th)
        return th

    def delete_threshold(self, threshold_id: int) -> bool:
        th = self.db.query(KRIThreshold).filter(KRIThreshold.id == threshold_id).first()
        if not th:
            return False
        self.db.delete(th)
        self.db.commit()
        return True

    # Schedule Operations
    def create_or_update_schedule(self, kri_id: int, schedule_in: KRIScheduleCreate) -> KRISchedule:
        sch = self.db.query(KRISchedule).filter(KRISchedule.kri_id == kri_id).first()
        if not sch:
            sch = KRISchedule(
                kri_id=kri_id,
                run_frequency=schedule_in.run_frequency,
                fetch_data_delay_days=schedule_in.fetch_data_delay_days,
                align_to_close_calendar=schedule_in.align_to_close_calendar,
                population_percentage=schedule_in.population_percentage,
                custom_date=schedule_in.custom_date,
            )
            self.db.add(sch)
        else:
            sch.run_frequency = schedule_in.run_frequency
            sch.fetch_data_delay_days = schedule_in.fetch_data_delay_days
            sch.align_to_close_calendar = schedule_in.align_to_close_calendar
            sch.population_percentage = schedule_in.population_percentage
            sch.custom_date = schedule_in.custom_date
            sch.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(sch)
        return sch

    # Reviewer Operations
    def add_reviewer(self, kri_id: int, reviewer_in: ReviewerCreate) -> Reviewer:
        rev = Reviewer(
            kri_id=kri_id,
            reviewer_name=reviewer_in.reviewer_name,
            reviewer_email=reviewer_in.reviewer_email,
            human_review_setting=reviewer_in.human_review_setting,
            lifecycle_status=reviewer_in.lifecycle_status,
        )
        self.db.add(rev)
        self.db.commit()
        self.db.refresh(rev)
        return rev
