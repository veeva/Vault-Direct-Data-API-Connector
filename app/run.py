import sys

sys.path.append('.')
from datetime import datetime, timezone, timedelta

import json
from typing import Dict

from common.redshift_setup import get_s3_path
from common.aws_utilities import invoke_lambda, start_batch_job
from common.direct_data_files_interface import list_direct_data_files, retrieve_direct_data_files, \
    unzip_direct_data_files, verify_redshift_tables
from common.integrationConfigClass import IntegrationConfigClass
from common.integrationRequestClass import IntegrationRequestClass
from common.log_message import log_message
from common.responseClass import ResponseClass


def lambda_handler(event, context):
    # Initializing an API response
    response: ResponseClass = ResponseClass(200, '')
    current_region = context.invoked_function_arn.split(":")[3]
    log_message(log_level='Info',
                message=f'Current region of lambda: {current_region}',
                context=None)
    # Retrieving variables from AWS Secrets Manager
    settings = IntegrationConfigClass(current_region)
    # Retrieving input parameters
    integration_request: IntegrationRequestClass = IntegrationRequestClass(event)
    step = integration_request.get_step()
    extract_type = integration_request.get_extract_type()
    continue_processing = bool(integration_request.get_continue_processing())

    if continue_processing is None or continue_processing == '':
        continue_processing = False

    log_message(log_level='Info',
                message=f'Starting Transaction with {step} step with {extract_type} extract type',
                context=None)

    s3_bucket = settings.config.get("s3", "bucket_name")
    s3_directory = settings.config.get("s3", "starting_directory")
    function_name = settings.config.get('lambda', 'function_name')

    if step == "retrieve":
        start_time = integration_request.get_start_time()
        stop_time = integration_request.get_stop_time()
        if extract_type == "incremental":
            try:
                if (start_time is None or start_time == '') and (stop_time is None or stop_time == ''):
                    stop_time = datetime.now(timezone.utc) - timedelta(minutes=15)
                    start_time = (stop_time - timedelta(minutes=15))
                    stop_time = stop_time.strftime("%Y-%m-%dT%H:%MZ")
                    start_time = start_time.strftime("%Y-%m-%dT%H:%MZ")
                else:
                    start_time = check_time_format(integration_request.get_start_time())
                    stop_time = check_time_format(integration_request.get_stop_time())
                log_message(log_level='Info',
                            message=f'Start time: {start_time} and stop time: {stop_time}',
                            context=None)
                list_of_direct_data_files_response = list_direct_data_files(start_time=start_time,
                                                                            stop_time=stop_time,
                                                                            extract_type=f'{extract_type}_directdata')
            except Exception as e:
                response.set_status_code(500)
                response.append_body(f'Error when trying to list direct data files:\n{e}')
                return response.to_dict()
            file_paths_retrieved: Dict[str, str] = {}

            if list_of_direct_data_files_response.is_successful() and bool(list_of_direct_data_files_response.data):
                for file in list_of_direct_data_files_response.data:
                    log_message(log_level='Info',
                                message=f'Iterating over direct data file list',
                                context=None)
                    log_message(log_level='Debug',
                                message=f'File name: {file.name} and record count: {file.record_count}',
                                context=None)
                    if file.record_count > 0:
                        file_paths_retrieved[file.name] = file.filename
            else:
                response.set_body("Nothing was returned when attempting to list the direct data files or there is an "
                                  "issue with the response")
            if len(file_paths_retrieved) > 0:
                for file_path_name, file_name in file_paths_retrieved.items():
                    try:
                        retrieval_success = retrieve_direct_data_files(
                            list_files_response=list_of_direct_data_files_response,
                            bucket_name=s3_bucket,
                            starting_directory=f'{s3_directory}/{file_name}')
                    except Exception as e:
                        response.set_status_code(500)
                        response.append_body(f'Error when trying to download direct data files: \n{e}')
                        return response.to_dict()
                    if continue_processing and retrieval_success:
                        payload: Dict[str, str] = {'step': 'unzip',
                                                   'source_filepath': f'{s3_directory}/{file_name}',
                                                   'target_filepath': f'{s3_directory}/{file_path_name}',
                                                   'continue_processing': f'{continue_processing}'}

                        try:
                            invoke_lambda(function_name=function_name, payload=json.dumps(payload))
                        except Exception as e:
                            response.set_status_code(500)
                            response.append_body(f'Error encountered when invoking AWS Lambda: \n{e}')
                            return response.to_dict()
                        response.append_body(f'Invoking AWS Lambda: {function_name} to unzip the retrieved files')
            else:
                response.set_body('No updates to be made')
        else:
            if start_time is not None and start_time != '':
                start_time = check_time_format(integration_request.get_start_time())
            else:
                start_time = ''
            if stop_time is not None and stop_time != '':
                stop_time = check_time_format(integration_request.get_stop_time())
            else:
                stop_time = ''
            job_name = settings.config.get('batch', 'job_name')
            job_queue = settings.config.get('batch', 'job_queue')
            job_definition = settings.config.get('batch', 'job_definition')
            job_parameter: Dict[str, str] = {'step': 'retrieve',
                                             'source_filepath': f'{s3_directory}',
                                             'continue_processing': 'true',
                                             'start_time': f'{start_time}',
                                             'stop_time': f'{stop_time}',
                                             'extract_type': f'{extract_type}'}

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

    elif step == "unzip":
        target_file_path = integration_request.get_target_directory()
        source_filepath = integration_request.get_source_file()
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
            if successful_unzip:
                function_name = settings.config.get('lambda', 'name')
                payload: Dict[str, str] = {'step': 'load_data',
                                           'source_file': f'{target_file_path}',
                                           'extract_type': 'incremental'}

                try:
                    invoke_lambda(function_name=function_name, payload=json.dumps(payload))
                    response.append_body(f'Invoking AWS Lambda: {function_name} to load the unzipped files')
                except Exception as e:
                    response.set_status_code(500)
                    response.append_body(f'Error encountered when invoking AWS Lambda: \n{e}')
                    return response.to_dict()
    elif step == "load_data":
        source_filepath = integration_request.get_source_file()
        vault_id = source_filepath.split("/")[-1].split("-")[0]
        schema_name = f'vault_{vault_id}'
        try:
            manifest_filepath = get_s3_path("manifest", s3_bucket, source_filepath)
            log_message(log_level='Debug',
                        message=f'The manifest file: {manifest_filepath}',
                        context=None)
            metadata_filepath = get_s3_path("metadata.csv", s3_bucket, source_filepath)
            if metadata_filepath is None or not metadata_filepath.strip():
                metadata_filepath = get_s3_path("metadata_full.csv", s3_bucket, source_filepath)
            log_message(log_level='Debug',
                        message=f'The metadata file: {metadata_filepath}',
                        context=None)
            metadata_deletes_filepath = get_s3_path("metadata_deletes.csv", s3_bucket, source_filepath)
        except Exception as e:
            response.set_status_code(500)
            response.append_body(f'Errors encountered when search for files in S3: \n{e}')
            return response.to_dict()
        try:
            verify_redshift_tables(chunk_size=500,
                                   bucket_name=s3_bucket,
                                   manifest_path=manifest_filepath,
                                   metadata_path=metadata_filepath,
                                   starting_directory=source_filepath,
                                   extract_type=extract_type,
                                   metadata_deletes_filepath=metadata_deletes_filepath,
                                   schema_name=schema_name)
        except Exception as e:
            response.set_status_code(500)
            response.append_body(f'Errors encountered when attempting to load the data:\n{e}')
            return response.to_dict()

        response.append_body('Successfully loaded Vault Direct Data into Redshift')

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
