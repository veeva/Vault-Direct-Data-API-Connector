import sys

from common.api.model.response.jobs_response import JobCreateResponse
from common.vault.vault_utility_interface import list_direct_data_files, retrieve_direct_data_files, export_documents, \
    download_documents_to_s3

sys.path.append('.')
import os

import json
from typing import Dict
from common.log_message import log_message
from common.aws_utilities import invoke_lambda, start_batch_job, get_batch_region, retrieve_doc_version_ids_from_s3, \
    check_file_exists_s3, get_s3_path
from common.vault.direct_data_files_interface import unzip_direct_data_files, verify_redshift_tables
from common.integrationConfigClass import IntegrationConfigClass
from common.api.model.response.direct_data_response import DirectDataResponse


def main():
    current_region = get_batch_region()
    log_message(log_level='Info',
                message=f'Current region of batch job: {current_region}',
                context=None)
    secret_name = os.environ.get("SECRET_NAME")
    secret = os.environ.get("SECRET")

    log_message(log_level='Info',
                message=f'Retrieving {secret} secret from {secret_name}',
                context=None)

    settings = IntegrationConfigClass(current_region, secret_name)
    step = os.environ.get("STEP")
    extract_type = os.environ.get("EXTRACT_TYPE")
    continue_processing = os.environ.get("CONTINUE_PROCESSING", "false").lower() == "true"

    log_message(log_level='Debug',
                message=f'Raw Continue Processing: {os.environ.get("CONTINUE_PROCESSING")}',
                context=None)

    if continue_processing is None:
        continue_processing = False

    log_message(log_level='Debug',
                message=f'Continue Processing: {continue_processing}',
                context=None)

    log_message(log_level='Info',
                message=f'Starting Transaction with {step} step of a {extract_type} extract',
                context=None)

    s3_bucket = settings.config.get(secret, "s3_bucket_name")
    s3_directory = settings.config.get(secret, "s3_starting_directory")

    job_name = settings.config.get(secret, 'job_name')
    job_queue = settings.config.get(secret, 'job_queue')
    job_definition = settings.config.get(secret, 'job_definition')

    if step == "retrieve":
        start_time = os.environ.get("START_TIME")
        stop_time = os.environ.get("STOP_TIME")
        log_message(log_level='Info',
                    message=f'Listing Direct Data files with start time: {start_time} and stop time: {stop_time}',
                    context=None)
        # List the the Direct Data files of the specified extract type and time window
        list_of_direct_data_files_response: DirectDataResponse = list_direct_data_files(start_time=str(start_time),
                                                                                        stop_time=str(stop_time),
                                                                                        extract_type=f'{extract_type}_directdata',
                                                                                        secret=secret,
                                                                                        settings=settings)

        # If the file listing was successful and the response is not empty, download the latest Direct Data file in
        # the response.
        if list_of_direct_data_files_response.is_successful() and bool(list_of_direct_data_files_response.data):
            direct_data_item = list_of_direct_data_files_response.data[-1]
            file_path_name = direct_data_item.name
            file_name = direct_data_item.filename
            retrieval_success = retrieve_direct_data_files(list_files_response=list_of_direct_data_files_response,
                                                           bucket_name=s3_bucket,
                                                           starting_directory=f'{s3_directory}/{file_name}',
                                                           secret_name=secret,
                                                           settings=settings)
            if retrieval_success:
                function_name = settings.config.get(secret, 'lambda_function_name')

                payload: Dict[str, str] = {'step': 'unzip',
                                           'source_filepath': f'{s3_directory}/{file_name}',
                                           'target_directory': f'{s3_directory}/{file_path_name}',
                                           'extract_type': f'{extract_type}',
                                           'continue_processing': f'{continue_processing}',
                                           'secret': f'{secret}'}

                invoke_lambda(function_name=function_name, payload=json.dumps(payload))

                log_message(log_level='Info',
                            message=f'Invoking {function_name} with unzip step',
                            context=None)

    elif step == "unzip":
        source_filepath = os.environ.get("SOURCE_FILEPATH")
        target_directory = os.environ.get("TARGET_DIRECTORY")
        log_message(log_level='Info',
                    message=f'Unzipping {source_filepath} to {target_directory}',
                    context=None)
        successful_unzip = unzip_direct_data_files(bucket_name=s3_bucket,
                                                   source_zipped_file_path=source_filepath,
                                                   target_filepath=f'{target_directory}/')

        if successful_unzip and continue_processing:
            function_name = settings.config.get(secret, 'lambda_function_name')
            payload: Dict[str, str] = {'step': 'load_data',
                                       'source_filepath': f'{target_directory}',
                                       'extract_type': f'{extract_type}',
                                       'secret': f'{secret}'}

            invoke_lambda(function_name=function_name, payload=json.dumps(payload))
            log_message(log_level='Info',
                        message=f'Invoking AWS Lambda {function_name} to load the data into Redshift',
                        context=None)

    elif step == "load_data":

        source_filepath = os.environ.get("SOURCE_FILEPATH")
        extract_source_content = os.environ.get("EXTRACT_SOURCE_CONTENT", "false").lower() == "true"

        if extract_source_content is None:
            extract_source_content = False

        log_message(log_level='Debug',
                    message=f'Source filepath: {source_filepath} and Extract Source content is {extract_source_content}',
                    context=None)

        # Generate the schema name with the given Vault ID from the Direct Data filename
        vault_id = source_filepath.split("/")[-1].split("-")[0]
        schema_name = f'vault_{vault_id}'
        try:
            # Get the S3 filepath of the manifest.csv file
            manifest_filepath = get_s3_path("manifest", s3_bucket, source_filepath)
            log_message(log_level='Debug',
                        message=f'The manifest file: {manifest_filepath}',
                        context=None)
            # Get the S3 filepath of the metadata.csv file
            metadata_filepath = get_s3_path("metadata.csv", s3_bucket, source_filepath)
            # If the metadata.csv file does not exist, then retrieve the metadata_full.csv filepath
            if metadata_filepath is None or not metadata_filepath.strip():
                metadata_filepath = get_s3_path("metadata_full.csv", s3_bucket, source_filepath)
            log_message(log_level='Info',
                        message=f'The metadata file: {metadata_filepath}',
                        context=None)
            # Get the S3 filepath of the metadata_deletes.csv file. This only exists in incremental extracts.
            metadata_deletes_filepath = get_s3_path("metadata_deletes.csv", s3_bucket, source_filepath)
        except Exception as e:
            log_message(log_level='Info',
                        message=f'Errors encountered when search for files in S3',
                        exception=e,
                        context=None)
        try:
            # Verify and subsequently load the Direct Data into the specified Redshift database that is specified in
            # the Secrets Manager.
            verify_redshift_tables(chunk_size=500,
                                   bucket_name=s3_bucket,
                                   manifest_path=manifest_filepath,
                                   metadata_path=metadata_filepath,
                                   starting_directory=source_filepath,
                                   extract_type=extract_type,
                                   metadata_deletes_filepath=metadata_deletes_filepath,
                                   schema_name=schema_name,
                                   settings=settings,
                                   extract_docs=extract_source_content,
                                   secret=secret)

            log_message(log_level='Info',
                        message='Successfully loaded Vault Direct Data into Redshift',
                        context=None)

        except Exception as e:
            log_message(log_level='Info',
                        message=f'Errors encountered when attempting to load the data',
                        exception=e,
                        context=None)

    elif step == "extract_docs":
        doc_version_id_filepath = os.environ.get("DOC_VERSION_IDS")
        starting_directory: str = os.environ.get("SOURCE_FILEPATH")
        if check_file_exists_s3(bucket_name=s3_bucket, file_key=doc_version_id_filepath[5:].split("/", 1)[1]):
            try:
                doc_version_ids = retrieve_doc_version_ids_from_s3(doc_version_id_filepath)

                log_message(log_level='Info',
                            message=f'Downloading {doc_version_ids} to {starting_directory}',
                            context=None)

                log_message(log_level='Info',
                            message=f'Exporting documents from Vault',
                            context=None)
                export_documents_response: JobCreateResponse = export_documents(doc_version_ids=doc_version_ids,
                                                                                secret=secret, settings=settings)

                if export_documents_response.responseStatus == 'SUCCESS':
                    log_message(log_level='Info',
                                message=f'Export successful. Downloading documents to S3',
                                context=None)
                    download_documents_to_s3(job_id=export_documents_response.job_id,
                                             target_path=f'{starting_directory}',
                                             bucket_name=s3_bucket, secret=secret, settings=settings)
                else:
                    log_message(log_level='Error',
                                message=f'Error encountered when attempting to export documents',
                                exception=Exception(export_documents_response.errors[0].message),
                                context=None)
                    raise Exception(export_documents_response.errors[0].message)
            except Exception as e:
                log_message(log_level='Error',
                            message=f'Errors encountered while exporting source content from Vault',
                            exception=e,
                            context=None)

            if check_file_exists_s3(bucket_name=s3_bucket, file_key=doc_version_id_filepath[5:].split("/", 1)[1]):

                job_parameter: Dict[str, str] = {'step': 'extract_docs',
                                                 'source_filepath': f'{starting_directory}',
                                                 'extract_type': 'incremental',
                                                 'doc_version_ids': f'{doc_version_id_filepath}'}

                start_batch_job(job_name=f'{job_name}-export', job_queue=job_queue,
                                job_definition=job_definition,
                                job_parameters=job_parameter)
            else:
                log_message(log_level='Info',
                            message=f'All documents exported successfully',
                            context=None)
        else:
            log_message(log_level='Info',
                        message=f'Document ID file does not exist',
                        context=None)
    log_message(log_level='Info',
                message=f'AWS Batch job finished successfully',
                context=None)


if __name__ == '__main__':
    main()
