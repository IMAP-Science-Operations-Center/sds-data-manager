from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    aws_events as events,
    aws_events_targets as event_targets,
    aws_lambda,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions
)
from libera_cdk.constructs.batch_compute_resources import FargateBatchResources
from libera_cdk.constructs.batch_system import BatchProcessingSystem


class SdcStepFunction(Construct):
    """Step Function Construct

    Creates state machine using processing components.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 processing_step_name: str,
                 processing_system: BatchProcessingSystem,
                 batch_resources: FargateBatchResources):
        """SdcStepFunction Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        processing_step_name : str
            The string identifier for the processing step
        processing_system: BatchProcessingSystem
            Batch processing system.
        db_secret_name : str
            The string secret id that can be used to retrieve from the SecretManager
        batch_resources: FargateBatchResources
            Fargate compute environment.
        """
        super().__init__(scope, construct_id)

        # Notification SNS topic with an emailed subscriber to be the reporting at the end
        # TODO think about how to properly subscribe to this topic
        self.reporting_topic = sns.Topic(self, "StepFunctionNotificationTopic",
                                         display_name=f"Libera SDP {processing_step_name} Notifications")
        self.reporting_topic.add_subscription(subscriptions.EmailSubscription("matt.watwood@lasp.colorado.edu"))

        ### Main flow Tasks
        # Reformat EventBridge Inputs
        add_specifics_to_input = sfn.Pass(
            self, "Reformat EventBridge Inputs",
            parameters={
                "STARTING_TIME.$": "$$.State.EnteredTime",
                "TRIGGER_SOURCE.$": "$.TriggerSource",
                "DATE_OF_INTEREST.$": "$.DateOfInterest",
                "ErrorSource": "None"
            }
        )

        create_timeout_time_task = tasks.LambdaInvoke(self, "Create Timeout",
                                                      lambda_function=processing_system.timeout_creator_lambda.lambda_function,
                                                      result_path='$.TimeoutCreatorOutput',
                                                      result_selector={
                                                          "STARTING_TIME.$": "$.Payload.STARTING_TIME",
                                                          "TIMEOUT_TIME.$": "$.Payload.TIMEOUT_TIME"
                                                      })

        # Step Functions Tasks to invoke Lambda function
        manifest_creator_task = tasks.LambdaInvoke(self, "Manifest Creator",
                                                   lambda_function=processing_system.manifest_creator_lambda.lambda_function,
                                                   payload=sfn.TaskInput.from_object(
                                                       {"TIMEOUT_TIME.$": "$.TimeoutCreatorOutput.TIMEOUT_TIME"}),
                                                   result_path="$.ManifestCreatorOutput",
                                                   result_selector={
                                                       "MANIFEST_STATE.$": "$.Payload.MANIFEST_STATE",
                                                       "INPUT_MANIFEST_FILENAME.$": "$.Payload.INPUT_MANIFEST_FILENAME",
                                                       "JOB_NAME.$": "$.Payload.JOB_NAME",
                                                       "COMMAND.$": "$.Payload.COMMAND",
                                                       "PERCENT_COMPLETE.$": "$.Payload.PERCENT_COMPLETE",
                                                       "DATA_PRODUCT_NAME.$": "$.Payload.DATA_PRODUCT_NAME",
                                                       "OUTPUT_MANIFEST_FILENAME.$": "$.Payload.OUTPUT_MANIFEST_FILENAME"
                                                   })

        # Batch Job Inputs
        stack = Stack.of(self)
        job_definition_arn = \
            f'arn:aws:batch:{stack.region}:{stack.account}:job-definition/{batch_resources.job_definition_name}'
        job_queue_arn = f'arn:aws:batch:{stack.region}:{stack.account}:job-queue/{batch_resources.job_queue_name}'

        # Batch Job Step Function
        processing_dropbox_path_str = f"s3://{processing_system.dropbox.bucket.bucket_name}/processing"
        submit_job = tasks.BatchSubmitJob(
            self, "Batch Job",
            job_name=sfn.JsonPath.string_at("$.ManifestCreatorOutput.JOB_NAME"),
            job_definition_arn=job_definition_arn,
            job_queue_arn=job_queue_arn,
            container_overrides=tasks.BatchContainerOverrides(
                command=sfn.JsonPath.list_at("$.ManifestCreatorOutput.COMMAND"),
                environment={
                    "PROCESSING_DROPBOX": processing_dropbox_path_str,
                    "LIBERA_DB_NAME": "rds-rdsinstance1d827d17-bh6pdezk66vz",
                }
            ),
            result_path='$.BatchJobOutput'
        )

        archiver_lambda_task = tasks.LambdaInvoke(self, "Archiver",
                                                  lambda_function=processing_system.finished_trigger_function.lambda_function,
                                                  payload=sfn.TaskInput.from_object({
                                                      "OUTPUT_MANIFEST_FILENAME.$":
                                                          "$.ManifestCreatorOutput.OUTPUT_MANIFEST_FILENAME",
                                                      "DATA_PRODUCT_NAME.$":
                                                          "$.ManifestCreatorOutput.DATA_PRODUCT_NAME"
                                                  }),
                                                  result_path='$.ArchiverOutput',
                                                  result_selector={
                                                      "OUTPUT_MANIFEST_FILENAME.$":
                                                          "$.Payload.OUTPUT_MANIFEST_FILENAME"
                                                  })

        # TODO: Create a Task state to invoke a Lambda function that will send an email when the process fails.
        # This Lambda function can use the AWS Simple Email Service (SES) or a third-party service like SendGrid to
        # send the email.First, define a new Lambda function that sends an email when invoked. This function can take
        # inputs from the state machine execution (like error messages or state outputs) and include them in the email.
        # Then, add a LambdaInvoke task before your Fail state. This task should invoke your new Lambda function.
        clean_up_lambda_task = tasks.LambdaInvoke(self, "Step Function Clean up and Mover",
                                                  lambda_function=processing_system.manifest_mover_lambda.lambda_function,
                                                  result_path='$.CleanupOutput',
                                                  result_selector={
                                                      "MESSAGE.$": "$.Payload.MESSAGE",
                                                      "STATUS.$": "$.Payload.STATUS"
                                                  })

        # Waiting on the Manifest Creation
        retry_wait_state = sfn.Wait(self, "Wait for 1 day and try again`", time=sfn.WaitTime.duration(Duration.days(1)))

        # Error Modifier Pass States
        timeout_creator_error_modifier = sfn.Pass(self, "Timeout Creator Error Modifier",
                                                  result_path="$.ErrorSource",
                                                  parameters={
                                                      "ERROR_SOURCE": "TimeoutCreator",
                                                      "Error.$": "$.TimeOutCreatorOutput.Error",
                                                      "Cause.$": "States.StringToJson($.TimeOutCreatorOutput.Cause)"
                                                  })

        manifest_creation_error = sfn.Pass(self, "Manifest Creation Error Modifier",
                                           result_path="$.ErrorSource",
                                           parameters={
                                               "ERROR_SOURCE": "ManifestCreatorOutput",
                                               "Error.$": "$.ManifestCreatorOutput.Error",
                                               "Cause.$": "States.StringToJson($.ManifestCreatorOutput.Cause)"
                                           })

        # Step Functions to Transform Data
        job_error_modifier = sfn.Pass(self, "Batch Job Error Modifier",
                                      result_path="$.ErrorSource",
                                      parameters={
                                          "ERROR_SOURCE": "BatchJobOutput",
                                          "Error.$": "$.BatchJobOutput.Error",
                                          "Cause.$": "States.StringToJson($.BatchJobOutput.Cause)"
                                      })

        archiver_error = sfn.Pass(self, "Archiver Error Modifier",
                                  result_path="$.ErrorSource",
                                  parameters={
                                      "ERROR_SOURCE": "ArchiverOutput",
                                      "Error.$": "$.ArchiverOutput.Error",
                                      "Cause.$": "States.StringToJson($.ArchiverOutput.Cause)"
                                  })

        clean_up_error = sfn.Pass(self, "Clean Up and Mover Error Modifier",
                                  result_path="$.ErrorSource",
                                  parameters={
                                      "ERROR_SOURCE": "CleanupOutput",
                                      "Error.$": "$.CleanupOutput.Error",
                                      "Cause.$": "States.StringToJson($.CleanupOutput.Cause)"
                                  })

        create_standard_message_task = tasks.EvaluateExpression(self, "Create standard error message",
                                                                expression="`${$.CleanupOutput.MESSAGE}`",
                                                                result_path="$.Message")

        cleanup_error_message = ("`The cleanup lambda failed. See State Machine. Error: ${$.ErrorSource.Error}"
                                 "Cause: ${$.ErrorSource.Cause.errorMessage}`")
        create_cleanup_message_task = tasks.EvaluateExpression(self, "Create cleanup error message",
                                                               expression=cleanup_error_message,
                                                               result_path="$.Message")

        publish_message_task = tasks.SnsPublish(self, "Publish message",
                                                topic=self.reporting_topic,
                                                message=sfn.TaskInput.from_json_path_at("$.Message"),
                                                result_path="$.SnsReportOutput")

        # Success and Fail Final States
        processing_step_success_state = sfn.Succeed(self, f"Succeed State")
        fail_state = sfn.Fail(self, "Fail State")

        # Waiting on the Manifest Creation
        wait_state = sfn.Wait(self, "Wait for more files (30 sec)", time=sfn.WaitTime.duration(Duration.seconds(30)))

        # Manifest Choice State
        manifest_status = sfn.Choice(self, "Enough Files for Manifest Creation?")
        # Manifest created go to secret retriever (Success)
        created = sfn.Condition.string_equals("$.ManifestCreatorOutput.MANIFEST_STATE", "FULL_SUCCESS")
        manifest_status.when(created, submit_job)
        # Timeout was reached with some data available (Partial Success)
        # TODO for SPICE/L1B etc. this may have a different path or notification. For now just goes to secret retriever
        timeout_with_files = sfn.Condition.string_equals("$.ManifestCreatorOutput.MANIFEST_STATE", "PARTIAL_SUCCESS")
        manifest_status.when(timeout_with_files, submit_job)
        # Waiting for more files go to wait state which goes back to manifest creator. (Hold State)
        waiting = sfn.Condition.string_equals("$.ManifestCreatorOutput.MANIFEST_STATE", "WAITING")
        manifest_status.when(waiting, wait_state.next(manifest_creator_task))
        # Otherwise head to the error state.
        manifest_status.otherwise(manifest_creation_error)

        final_output_choice_state = sfn.Choice(self, "Results of the Process?")
        final_output_choice_state.when(sfn.Condition.string_equals("$.CleanupOutput.STATUS", "SUCCEEDED"),
                                       processing_step_success_state)
        final_output_choice_state.when(sfn.Condition.string_equals("$.CleanupOutput.STATUS", "FAILED"),
                                       create_standard_message_task)
        final_output_choice_state.when(sfn.Condition.string_equals("$.CleanupOutput.STATUS", "RETRY"),
                                       retry_wait_state.next(add_specifics_to_input))

        # State sequences
        add_specifics_to_input.next(
            create_timeout_time_task).next(
            manifest_creator_task).next(
            manifest_status)

        job_error_modifier.next(
            clean_up_lambda_task)

        submit_job.next(
            archiver_lambda_task).next(
            clean_up_lambda_task)

        timeout_creator_error_modifier.next(
            clean_up_lambda_task)
        manifest_creation_error.next(
            clean_up_lambda_task)
        archiver_error.next(
            clean_up_lambda_task)

        clean_up_lambda_task.next(
            final_output_choice_state)

        clean_up_error.next(
            create_cleanup_message_task).next(
            publish_message_task)

        create_standard_message_task.next(
            publish_message_task).next(
            fail_state)

        # Add catches
        create_timeout_time_task.add_catch(timeout_creator_error_modifier, result_path='$.TimeOutCreatorOutput')
        manifest_creator_task.add_catch(manifest_creation_error, result_path='$.ManifestCreatorOutput')
        submit_job.add_catch(job_error_modifier, result_path='$.BatchJobOutput')
        archiver_lambda_task.add_catch(archiver_error, result_path='$.ArchiverOutput')
        clean_up_lambda_task.add_catch(clean_up_error, result_path='$.CleanupOutput')

        # Define the state machine
        definition_body = sfn.DefinitionBody.from_chainable(add_specifics_to_input)
        self.state_machine = sfn.StateMachine(self, "CDKProcessingStepStateMachine",
                                              definition_body=definition_body,
                                              state_machine_name=f"{processing_step_name}-processing-step-function")

