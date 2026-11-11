"""CoDICE LUT transition selection against an in-memory ancillary catalog."""

import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode.database.models import AncillaryFiles
from sds_data_manager.orchestration.custom_behavior.codice import CodiceL1aJob
from sds_data_manager.orchestration.imap_job import (
    IMAPJobHandler,
    MissingDependenciesError,
)
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry


@pytest.fixture
def catalog():
    """Use the real SQL selector with a small local catalog."""
    engine = create_engine("sqlite://")
    AncillaryFiles.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_lut(session, start, end=None, version="v001", descriptor="l1a-sci-lut"):
    """Add one indexed delivery without contacting SDC."""
    dates = start + (f"_{end}" if end else "")
    name = f"imap_codice_{descriptor}_{dates}_{version}.json"
    session.add(
        AncillaryFiles(
            file_path=f"imap/ancillary/codice/{name}",
            instrument="codice",
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start, "%Y%m%d"),
            end_date=datetime.datetime.strptime(end, "%Y%m%d") if end else None,
            version=version,
            extension="json",
        )
    )
    session.flush()
    return name


def handler(cls=CodiceL1aJob, descriptors=("l1a-sci-lut",)):
    """Build only the dependency-selection portion of a handler."""
    job = cls.__new__(cls)
    job.job_config = SimpleNamespace(
        inputs=[
            SimpleNamespace(
                source="codice",
                data_type="ancillary",
                descriptor=d,
                required=True,
                to_dagster_name=lambda: "codice_ancillary_lut",
            )
            for d in descriptors
        ]
    )
    return job


def select(job, session, day):
    """Select the inputs of a daily partition."""
    start = datetime.datetime.strptime(day, "%Y%m%d")
    return job.get_ancillary_files_inputs(
        session, start, start + datetime.timedelta(days=1)
    )


def test_transition_keeps_latest_previous_delivery(catalog):
    """December 18 needs the old table despite its December 17 end date."""
    add_lut(catalog, "20251007", version="v009")
    add_lut(catalog, "20251124", "20251217")
    previous = add_lut(catalog, "20251124", "20251217", "v003")
    add_lut(catalog, "20251218")
    current = add_lut(catalog, "20251218", version="v002")
    add_lut(catalog, "20260129", version="v010")
    assert select(handler(), catalog, "20251218")[0].filename_list == [
        current,
        previous,
    ]
    # The generic selector remains unchanged for other instruments/products.
    assert select(handler(IMAPJobHandler), catalog, "20251218")[0].filename_list == [
        current
    ]


def test_non_transition_day_keeps_one_lut(catalog):
    """Do not retain old deliveries indefinitely."""
    add_lut(catalog, "20251124", "20251217")
    current = add_lut(catalog, "20251218")
    assert select(handler(), catalog, "20251219")[0].filename_list == [current]


def test_first_delivery_and_other_ancillaries(catalog):
    """A missing predecessor is fine; unrelated inputs retain normal selection."""
    current = add_lut(catalog, "20251218")
    other = add_lut(catalog, "20251218", descriptor="l2-lo-gfactor")
    inputs = select(
        handler(descriptors=("l1a-sci-lut", "l2-lo-gfactor")), catalog, "20251218"
    )
    assert [item.filename_list for item in inputs] == [[current], [other]]


def test_missing_required_lut_still_fails(catalog):
    """A predecessor must not mask missing current coverage."""
    add_lut(catalog, "20251124", "20251217")
    with pytest.raises(MissingDependenciesError):
        select(handler(), catalog, "20251218")


def test_codice_l1a_handler_registered():
    """Use the override only for the daily CoDICE L1A all job."""
    assert JobBuilderRegistry._registry[("codice", "l1a", "all")] is CodiceL1aJob
