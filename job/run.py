import sys

from common.log_message import log_message

sys.path.append('.')
import os

import json
from typing import Dict

from common.aws_utilities import invoke_lambda, start_batch_job, get_batch_region
from common.direct_data_files_interface import list_direct_data_files, retrieve_direct_data_files, \
    unzip_direct_data_files
from common.integrationConfigClass import IntegrationConfigClass
from common.api.model.response.direct_data_response import DirectDataResponse


def main():
    current_region = get_batch_region()
    log_message(log_level='Info',
                message=f'Current region of batch job: {current_region}',
                context=None)
    settings = IntegrationConfigClass(current_region)
    step = os.environ.get("STEP")
    extract_type = os.environ.get("EXTRACT_TYPE")
    continue_processing = os.environ.get("CONTINUE_PROCESSING")

    log_message(log_level='Info',
                message=f'Starting Transaction with {step} step of a {extract_type} extract',
                context=None)

    s3_bucket = settings.config.get("s3", "bucket_name")
    s3_directory = settings.config.get("s3", "starting_directory")

    if step == "retrieve":
        start_time = os.environ.get("START_TIME")
        stop_time = os.environ.get("STOP_TIME")
        log_message(log_level='Info',
                    message=f'Listing Direct Data files with start time: {start_time} and stop time: {stop_time}',
                    context=None)
        list_of_direct_data_files_response: DirectDataResponse = list_direct_data_files(start_time=str(start_time),
                                                                                        stop_time=str(stop_time),
                                                                                        extract_type=f'{extract_type}_directdata')

        if list_of_direct_data_files_response.is_successful() and bool(list_of_direct_data_files_response.data):
            direct_data_item = list_of_direct_data_files_response.data[-1]
            file_path_name = direct_data_item.name
            file_name = direct_data_item.filename
            retrieval_success = retrieve_direct_data_files(list_files_response=list_of_direct_data_files_response,
                                                           bucket_name=s3_bucket,
                                                           starting_directory=f'{s3_directory}/{file_name}')
            if retrieval_success:
                job_name = settings.config.get('batch', 'job_name')
                job_queue = settings.config.get('batch', 'job_queue')
                job_definition = settings.config.get('batch', 'job_definition')
                job_parameter: Dict[str, str] = {'step': 'unzip',
                                                 'source_filepath': f'{s3_directory}/{file_name}',
                                                 'target_filepath': f'{s3_directory}/{file_path_name}',
                                                 'extract_type': f'{extract_type}',
                                                 'continue_processing': f'{continue_processing}'}

                batch_job_response = start_batch_job(job_name=f'{job_name}-unzip', job_queue=job_queue,
                                                     job_definition=job_definition,
                                                     job_parameters=job_parameter)

                log_message(log_level='Info',
                            message=f'Starting {job_name} with ID: {batch_job_response["jobId"]} to unzip files',
                            context=None)

    elif step == "unzip":
        source_filepath = os.environ.get("SOURCE_FILEPATH")
        target_filepath = os.environ.get("TARGET_FILEPATH")
        log_message(log_level='Info',
                    message=f'Unzipping {source_filepath} to {target_filepath}',
                    context=None)
        successful_unzip = unzip_direct_data_files(bucket_name=s3_bucket,
                                                   source_zipped_file_path=source_filepath,
                                                   target_filepath=f'{target_filepath}/')

        if successful_unzip and continue_processing:
            function_name = settings.config.get('lambda', 'function_name')
            payload: Dict[str, str] = {'step': 'load_data',
                                       'source_file': f'{target_filepath}',
                                       'extract_type': f'{extract_type}'}

            invoke_lambda(function_name=function_name, payload=json.dumps(payload))
            log_message(log_level='Info',
                        message=f'Invoking AWS Lambda {function_name} to load the data into Redshift',
                        context=None)

    log_message(log_level='Info',
                message=f'AWS Batch job finished successfully',
                context=None)


if __name__ == '__main__':
    main()
