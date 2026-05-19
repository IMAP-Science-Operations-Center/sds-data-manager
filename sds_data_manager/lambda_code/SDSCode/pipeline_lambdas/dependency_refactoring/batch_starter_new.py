"""IMAP job handler for managing dependencies and job submission."""

import hashlib
import json
import logging

from imap_data_access import DependencyFilePath, ProcessingInputCollection
from sqlalchemy import func

from sds_data_manager.lambda_code.SDSCode.database import database as db

from ...database import models
from .dependency_new import DependencyResolver, upload_dependency_file
from .types import DependencyNode, ProcessingJobNode

logger = logging.getLogger(__name__)


def get_dependencies(
    dependency_node: DependencyNode, job_node: ProcessingJobNode
) -> ProcessingInputCollection:
    """Get the dependencies for the job using the DependencyResolver."""
    with db.Session() as session:
        response = DependencyResolver().get_upstream_dependency(
            session=session, input_upstream_node=dependency_node
        )

        collection = ProcessingInputCollection()
        # Expecting a return of {"status": int, "data": ProcessingInputCollection}
        # If dependency status is 200, then it means that we have complete set of
        # dependencies needed.
        if response["status"] == 200:
            return collection.deserialize(json.dumps(response["data"]))

    return collection


def process_job(potential_job_node: ProcessingJobNode, time_range):
    """Process the job by resolving dependencies and submitting to batch."""
    # TODO: potential_job_node represents a range of expected jobs.
    # Loop here through each 24 hour span or reprocessing.

    dependency_node = determine_dependency_node(potential_job_node, time_range)
    dependencies = get_dependencies(dependency_node)
    if dependencies.processing_input:
        crid = dependency_hash(dependencies.serialize())
        version = determine_job_version()

        create_and_upload_dependencies_file(
            node=potential_job_node,
            dependencies=dependencies,
            crid=crid,
            version=version,
        )
        # Next: submit the actual job, using submit_job


def determine_dependency_node(processing_job: ProcessingJobNode, time_range):
    """Find the dependency node for a processing job."""
    return DependencyNode()


def dependency_hash(dependencies: str | ProcessingInputCollection):
    """Generate a hash for the serialized dependencies. Use only the first 8 characters.

    This hash (also known as a CRID) is used to determine if a job with the same input
    dependencies, including versions, has already been created.

    Parameters
    ----------
    dependencies : str | ProcessingInputCollection
        The serialized dependencies string OR the ProcessingInputCollection. Either way,
        the hash will use the output of ProcessingInputCollection.

    Returns
    -------
    str
        The first 8 characters of the SHA-256 hash of the serialized dependencies.
    """
    serialized_dependencies = dependencies
    # TODO: update serialize to sort the files before sending it here, to ensure
    # better consistency
    if isinstance(dependencies, ProcessingInputCollection):
        serialized_dependencies = dependencies.serialize()

    return hashlib.sha256(serialized_dependencies.encode("utf-8")).hexdigest()[:8]


def determine_job_version(session: db.Session, node: ProcessingJobNode):
    """Return the next version number for this job (max version + 1).

    This always returns an incremented version. We don't need dependencies here
    because the unique constraint on (dependency_hash, container_image_digest)
    handles duplicate detection reactively when submitting a new job.

    The duplicate detection works like this: a job is skipped only if BOTH the
    dependency_hash AND container_image_digest are identical to a previous
    INPROGRESS or SUCCEEDED job. If either the hash changes or the image digest changes
    (due to a new software version), a new job submission is allowed with a bumped
    version.

    Priority order:
    1. in-process jobs from the processing table
    2. science files table
    3. completed jobs (if descriptor is all or pointing_attitude)
    4. v001
    Function returns as soon as it finds a valid version, going through each option.

    Parameters
    ----------
    session : orm session
        Database session.
    node: ProcessingJobNode
        Node containing all information required for creating a job.

    Returns
    -------
     str
        The highest version number, in the format "v000".

    """

    def filter_conditions(table):
        # Filter conditions for the query
        conditions = [
            table.instrument == node.source,
            table.data_level == node.data_type,
            table.descriptor == node.descriptor,
        ]
        if not node.time_span.pointing_number_start:
            conditions.append(table.start_date == node.time_span.start_time)
        else:
            # If repointing is used, use that instead of the start date.
            conditions.append(table.repoint == node.time_span.pointing_number_start)

        if table == models.ProcessingJob:
            conditions.append(
                table.status.in_(
                    [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                )
            )
        return conditions

    # By default, use the max version from the science files table unless
    # it is a spacecraft "pointing-attitude" job or an "all" descriptor.

    # If the job is a spacecraft
    # pointing-attitude job, it will produce a SPICE kernel and not a science file.
    # There is no way to determine the filename of the kernel that will be produced.

    # if it is "all" descriptor, this won't correspond to any science files.
    # In both cases, we rely on the max version from the processing jobs table.
    use_job_table = ["pointing-attiude", "all"]

    # query to get the max version from the processing jobs table
    max_version_record = (
        session.query(models.ProcessingJob)
        .filter(*filter_conditions(models.ProcessingJob))
        .order_by(models.ProcessingJob.version.desc())
        .first()
    )

    if max_version_record:
        max_version_processing = max_version_record.version
        # First try: If there is a job already in progress, return the next
        # version number without checking the science files table. This is to
        # avoid filename
        # collisions between two jobs running at the same time with the same version
        # number. The unique constraint on (dependency_hash, container_image_digest)
        # will prevent the job from being submitted if it is a true duplicate.
        if max_version_record.status == models.Status.INPROGRESS:
            logger.info(
                f"While determining version, found a job with id: "
                f"{max_version_record.id} in progress."
            )
            return f"v{int(max_version_processing[1:]) + 1:03d}"

    # Second Try: Use the science files table
    if node.descriptor not in use_job_table:
        max_version_sci = (
            session.query(func.max(models.ScienceFiles.version)).filter(
                *filter_conditions(models.ScienceFiles)
            )
        ).scalar()

        return f"v{int(max_version_sci[1:]) + 1:03d}"

    # third try: use the maximum version found in completed processing
    if max_version_processing:
        return f"v{int(max_version_processing[1:]) + 1:03d}"

    return "v001"


def create_and_upload_dependencies_file(
    node: ProcessingJobNode,
    dependencies: ProcessingInputCollection,
    crid: str,
    version: str,
) -> str | None:
    """Build the dependency file path, upload it to S3, and return the S3 path.

    The dependency file is a JSON file containing the serialized upstream
    dependencies and is used as CLI input when submitting an AWS Batch job.
    The file descriptor embeds the CRID so that jobs with different dependency
    sets produce distinct S3 keys.

    Parameters
    ----------
    node : ProcessingJobNode
        The planned processing node supplying instrument, data level,
        descriptor, start date, and (optionally) repoint number.
    dependencies : ProcessingInputCollection
        The resolved upstream dependencies to serialize and upload.
    crid : str
        8-character dependency hash (CRID) used to distinguish dependency sets
        with the same instrument/level/descriptor/date.
    version : str
        Job version string in "v000" format, e.g. "v001".

    Returns
    -------
    str or None
        The S3 path of the uploaded dependency file, or None if the upload
        failed (in which case job submission should be skipped).
    """
    dep_descriptor = f"{node.descriptor}-{crid}"
    dependency_file = DependencyFilePath.generate_from_inputs(
        instrument=node.source,
        data_level=node.data_type,
        descriptor=dep_descriptor,
        start_time=node.time_span.start_time,
        version=version,
        extension="json",
        repointing=node.time_span.pointing_number_start,
    )
    dependency_file_path = dependency_file.construct_path()
    response = upload_dependency_file(dependency_file_path, dependencies.serialize())
    if not response:
        return None
    return str(dependency_file_path)


def submit_job(
    session: db.Session,
    node: ProcessingJobNode,
):
    """Submit the job for processing.

    This first retrieves data needed for the processing table, such as the container
    id. Then, it attempts to add the job to the processing table, and checks for
    duplicate runs there. Then, it assembles the batch job using ProcessingJobNode and
    submits it.

    """
    # Copied out of batch_starter - refactoring is not complete, but this
    # represents what will be used.

    # if repoint is not None:
    #     batch_command.extend(["--repointing", f"repoint{repoint:05d}"])
    # # Get the necessary AWS information
    # # NOTE: These are here for easier mocking in tests rather than at the module level
    # step = "-l3" if data_level >= "l3" else ""
    # job_definition = f"ProcessingJob-{instrument}{step}"

    # # Capture the container image and digest right before submitting the job.
    # # This ensures the image digest that will be used is recorded. We record this
    # # information here and not in indexer.py to avoid race conditions where the image
    # # could change during job execution.
    # container_image_digest = get_container_image_digest(job_definition)

    # # All of our upstream requirements have been met.
    # # Try to insert a record into the Processing Jobs table
    # # If this job already exists, then we will get an integrity error
    # # and know that some other process has already taken care of it
    # processing_job = models.ProcessingJob(
    #     status=models.Status.INPROGRESS,
    #     instrument=instrument,
    #     data_level=data_level,
    #     descriptor=descriptor,
    #     start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
    #     version=version,
    #     repointing=repoint,
    #     dependency_hash=dep_hash,
    #     container_command=" ".join(batch_command),
    #     container_image_digest=container_image_digest,
    # )
    # try:
    #     session.add(processing_job)
    #     session.commit()
    # except IntegrityError:
    #     # Rollback the session to clear the failed transaction
    #     session.rollback()
    #     logger.info(
    #         f"Job already completed or in progress. Tried to submit "
    #         f"{processing_job.to_dict()}"
    #     )
    #     raise ValueError

    # logger.info(
    #     f"Wrote job INPROGRESS to Processing Jobs Table with id: {processing_job.id}"
    # )
    # # NOTE: The batch job name should contain only alphanumeric characters and hyphens
    # # E.g. "codice-l1a-sci-job-1"
    # # The `processing_job.id` is used later for updating the job processing table
    # job_name = f"{instrument}-{data_level}-{descriptor}-job-{processing_job.id}"
    # job_queue = "ProcessingJobQueue"

    # BATCH_CLIENT.submit_job(
    #     jobName=job_name,
    #     jobQueue=job_queue,
    #     jobDefinition=job_definition,
    #     containerOverrides={
    #         "command": batch_command,
    #     },
    #     retryStrategy=BATCH_JOB_RETRY_STRATEGY,
    # )
    # logger.info(f"Submitted job {job_name} with this command: {batch_command}")

    raise NotImplementedError
