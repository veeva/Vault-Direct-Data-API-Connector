import os
import sys

sys.path.append('.')
from datetime import datetime, timezone, timedelta

from typing import Dict
from common.aws_utilities import start_batch_job
from common.vault.direct_data_files_interface import unzip_direct_data_files
from common.integrationConfigClass import IntegrationConfigClass
from common.integrationRequestClass import IntegrationRequestClass
from common.log_message import log_message
from common.responseClass import ResponseClass


def lambda_handler(event, context):
    # Initializing an API response
    response: ResponseClass = ResponseClass(200, '')
    current_region = context.invoked_function_arn.split(":")[3]
    secret_name = os.environ.get("SECRET_NAME")
    log_message(log_level='Info',
                message=f'Current region of lambda: {current_region}',
                context=None)
    # Retrieving variables from AWS Secrets Manager
    settings = IntegrationConfigClass(current_region, secret_name)
    # Retrieving input parameters
    integration_request: IntegrationRequestClass = IntegrationRequestClass(event)
    step = integration_request.get_step()
    extract_type = integration_request.get_extract_type()
    continue_processing = bool(integration_request.get_continue_processing())
    secret = integration_request.get_secret()


    log_message(log_level='Debug',
                message=f'Continue Processing: {continue_processing}',
                context=None)

    if continue_processing is None or continue_processing == '':
        continue_processing = False

        log_message(log_level='Debug',
                    message=f'Continue Processing after null check: {continue_processing}',
                    context=None)

    log_message(log_level='Info',
                message=f'Starting Transaction with {step} step with {extract_type} extract type',
                context=None)

    log_message(log_level='Debug',
                message=f'Secret name: {secret_name} and Secret block: {secret}',
                context=None)

    s3_bucket = settings.config.get(secret, "s3_bucket_name")
    s3_directory = settings.config.get(secret, "s3_starting_directory")

    job_name = settings.config.get(secret, 'job_name')
    job_queue = settings.config.get(secret, 'job_queue')
    job_definition = settings.config.get(secret, 'job_definition')

    # Retrieving Direct Data Files
    if step == "retrieve":
        start_time = integration_request.get_start_time()
        stop_time = integration_request.get_stop_time()
        # If the start_time and stop_time are empty, set the time difference to 15 minutes for incremental or 1 day
        # for log extracts and format appropriately
        if (start_time is None or start_time == '') and (stop_time is None or stop_time == ''):
            if extract_type == "incremental":
                stop_time = datetime.now(timezone.utc) - timedelta(minutes=15)
                start_time = (stop_time - timedelta(minutes=15))
            elif extract_type == "log":
                stop_time = datetime.now(timezone.utc) - timedelta(days=1)
                start_time = (stop_time - timedelta(days=1))
            stop_time = stop_time.strftime("%Y-%m-%dT%H:%MZ")
            start_time = start_time.strftime("%Y-%m-%dT%H:%MZ")
        else:
            start_time = check_time_format(start_time)
            stop_time = check_time_format(stop_time)

        # Form job paramters and submit Batch job to unzip the retieved files
        job_parameter: Dict[str, str] = {'step': 'retrieve',
                                         'source_filepath': f'{s3_directory}',
                                         'continue_processing': f'{continue_processing}',
                                         'start_time': f'{start_time}',
                                         'stop_time': f'{stop_time}',
                                         'extract_type': f'{extract_type}',
                                         'secret_name': f'{secret_name}',
                                         'secret': f'{secret}'}

        log_message(log_level='Debug',
                    message=f'Job Parameters: {job_parameter}',
                    context=None)

        try:
            batch_job_response = start_batch_job(job_name=f'{job_name}-retrieve', job_queue=job_queue,
                                                 job_definition=job_definition,
                                                 job_parameters=job_parameter)
        except Exception as e:
            response.set_status_code(500)
            response.append_body(f'Error encountered when attempting to stary AWS Batch job: \n{e}')
            return response.to_dict()

        response.set_body(f'Starting AWS Batch Job with ID: {batch_job_response["jobName"]}')

    # Unzip previously retrieved Direct Data files
    elif step == "unzip":
        target_file_path = integration_request.get_target_directory()
        source_filepath = integration_request.get_source_filepath()

        # If the extract type is incremental, then just extract in the lambda function, otherwise submit a Batch job
        # to unzip.
        if extract_type == "incremental":
            log_message(log_level='Info',
                        message=f'Unzipping {source_filepath} to {target_file_path}/',
                        context=None)
            try:
                successful_unzip = unzip_direct_data_files(s3_bucket, source_filepath,
                                                           f'{integration_request.get_target_directory()}/')
            except Exception as e:
                response.set_status_code(500)
                response.append_body(f'Errors encountered when attempting to unzip files {source_filepath}\n{e}')
                return response.to_dict()
            if continue_processing:
                # If the unzipping is successful, form job parameters and submit a Batch job to load the data
                if successful_unzip:

                    job_parameter: Dict[str, str] = {'step': 'load_data',
                                                     'source_filepath': f'{target_file_path}',
                                                     'extract_type': f'{extract_type}',
                                                     'secret_name': f'{secret_name}',
                                                     'secret': f'{secret}'}

                    try:
                        start_batch_job(job_name=f'{job_name}-load', job_queue=job_queue,
                                        job_definition=job_definition,
                                        job_parameters=job_parameter)
                    except Exception as e:
                        response.set_status_code(500)
                        response.append_body(f'Error encountered when attempting to starting AWS Batch job: \n{e}')
                        return response.to_dict()
        else:

            job_parameter: Dict[str, str] = {'step': 'unzip',
                                             'source_filepath': f'{source_filepath}',
                                             'target_directory': f'{target_file_path}',
                                             'extract_type': f'{extract_type}',
                                             'continue_processing': f'{continue_processing}',
                                             'secret_name': f'{secret_name}',
                                             'secret': f'{secret}'}

            log_message(log_level='Debug',
                        message=f'Job Parameters: {job_parameter}',
                        context=None)


            try:
                batch_job_response = start_batch_job(job_name=f'{job_name}-unzip', job_queue=job_queue,
                                                     job_definition=job_definition,
                                                     job_parameters=job_parameter)
                log_message(log_level='Info',
                            message=f'Starting {job_name} with ID: {batch_job_response["jobId"]} to unzip files',
                            context=None)

            except Exception as e:
                response.set_status_code(500)
                response.append_body(f'Error encountered when attempting to starting AWS Batch job: \n{e}')
                return response.to_dict()

    # Load the extracted Direct Data files into a specified Redshift database via a Batch job
    elif step == "load_data":

        source_filepath = integration_request.get_source_filepath()
        extract_source_content = os.environ.get("EXTRACT_SOURCE_CONTENT")

        log_message(log_level='Info',
                    message=f'Loading data from {source_filepath}',
                    context=None)

        job_parameter: Dict[str, str] = {'step': 'load_data',
                                         'source_filepath': f'{source_filepath}',
                                         'extract_source_content': f'{extract_source_content}',
                                         'extract_type': f'{extract_type}',
                                         'secret_name': f'{secret_name}',
                                         'secret': f'{secret}'}

        try:
            log_message(log_level='Info',
                        message=f'Starting {job_name}-load job in the {job_queue} with {job_definition} definition and {job_parameter} parameters',
                        context=None)
            start_batch_job(job_name=f'{job_name}-load', job_queue=job_queue,
                            job_definition=job_definition,
                            job_parameters=job_parameter)
        except Exception as e:
            response.set_status_code(500)
            response.append_body(f'Error encountered when attempting to starting AWS Batch job: \n{e}')
            return response.to_dict()

    response.append_body(f'Completed {step} step.')
    return response.to_dict()


def check_time_format(date: str) -> str:
    try:
        # Attempt to parse the time string
        parsed_time = datetime.strptime(date, "%Y-%m-%dT%H:%MZ")

        # Check if the parsed time matches the original string
        if not date == parsed_time.strftime("%Y-%m-%dT%H:%MZ"):
            date.strftime("%Y-%m-%dT%H:%MZ")
        return date
    except ValueError as e:
        log_message(log_level='Error',
                    message=f'Error encountered converting time format',
                    exception=e,
                    context=None)
        raise e


if __name__ == '__main__':
    lambda_handler('', '')
