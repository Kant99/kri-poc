"""Repository for KRI Configuration, Steps, Thresholds, Schedules, and Data Sources."""

from datetime import datetime
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from app.models.kri import (
    KRI,
    ProcessArea,
    DataSource,
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
    def get_or_create_data_source(self, name: str, code: str, system_type: str, description: Optional[str] = None) -> DataSource:
        ds = self.db.query(DataSource).filter(DataSource.code == code).first()
        if not ds:
            ds = DataSource(name=name, code=code, system_type=system_type, description=description)
            self.db.add(ds)
            self.db.commit()
            self.db.refresh(ds)
        return ds

    def list_data_sources(self) -> List[DataSource]:
        return self.db.query(DataSource).all()

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
    def add_test_step(self, kri_id: int, step_in: KRITestStepCreate) -> KRITestStep:
        step = KRITestStep(
            kri_id=kri_id,
            step_number=step_in.step_number,
            title=step_in.title,
            instruction=step_in.instruction,
            is_active=step_in.is_active,
        )
        self.db.add(step)
        self.db.commit()
        self.db.refresh(step)
        return step

    def update_test_step(self, step_id: int, step_in: KRITestStepUpdate) -> Optional[KRITestStep]:
        step = self.db.query(KRITestStep).filter(KRITestStep.id == step_id).first()
        if not step:
            return None
        if step_in.title is not None:
            step.title = step_in.title
        if step_in.instruction is not None:
            step.instruction = step_in.instruction
        if step_in.is_active is not None:
            step.is_active = step_in.is_active
        step.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(step)
        return step

    def delete_test_step(self, step_id: int) -> bool:
        step = self.db.query(KRITestStep).filter(KRITestStep.id == step_id).first()
        if not step:
            return False
        self.db.delete(step)
        self.db.commit()
        return True

    def reorder_test_steps(self, kri_id: int, reorder_list: List[StepReorderItem]) -> List[KRITestStep]:
        for item in reorder_list:
            step = self.db.query(KRITestStep).filter(KRITestStep.id == item.step_id, KRITestStep.kri_id == kri_id).first()
            if step:
                step.step_number = item.new_step_number
                step.updated_at = datetime.utcnow()
        self.db.commit()
        return self.db.query(KRITestStep).filter(KRITestStep.kri_id == kri_id).order_by(KRITestStep.step_number).all()

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
