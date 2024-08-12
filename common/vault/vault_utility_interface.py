import json
import time
import urllib.parse
from pathlib import Path
from typing import List

import boto3
from botocore.client import BaseClient

from common.api.client.vault_client import VaultClient, AuthenticationType
from common.api.model.response.authentication_response import AuthenticationResponse
from common.api.model.response.direct_data_response import DirectDataResponse
from common.api.model.response.document_response import DocumentExportResponse
from common.api.model.response.jobs_response import JobCreateResponse
from common.api.model.response.vault_response import VaultResponse
from common.api.request.authentication_request import AuthenticationRequest
from common.api.request.direct_data_request import DirectDataRequest, ExtractType
from common.api.request.file_staging_request import FileStagingRequest
from common.api.request.document_request import DocumentRequest
from common.integrationConfigClass import IntegrationConfigClass
from common.log_message import log_message
from common.responseClass import ResponseClass

vault_client: VaultClient = None


def get_vault_client(settings, secret) -> VaultClient | ResponseClass:
    """
    This generates a Vault Client from Vapil.py given the proper credentials for the target Vault.
    :return: A valid, authenticated Vault Client
    """
    global vault_client

    try:
        if vault_client is None:
            vault_client = VaultClient(
                vault_client_id='Veeva-Vault-DevSupport-Direct-Data',
                vault_username=settings.config.get(secret, "vault_username"),
                vault_password=settings.config.get(secret, "vault_password"),
                vault_dns=settings.config.get(secret, "vault_dns"),
                authentication_type=AuthenticationType.BASIC)
        else:
            if vault_client.validate_session(auth_request=vault_client.new_request(AuthenticationRequest)):
                return vault_client

        log_message(log_level='Info',
                    message='Vault Client is attempting to authenticate',
                    context=None)

        vault_client.authenticate()

        auth_response: AuthenticationResponse = vault_client.authentication_response

        if auth_response.responseStatus == "SUCCESS":
            return vault_client
        else:
            log_message(log_level='Error',
                        message=f'Vault Client failed to authenticate: {auth_response.errors[0].message}',
                        context=None)
            return ResponseClass(500, auth_response.errors[0].message)

    except Exception as e:
        log_message(log_level='Error',
                    message=f'Could not authenticate',
                    exception=e,
                    context=None)
        print(e)
        return ResponseClass(500, e)


def retrieve_direct_data_files(list_files_response: DirectDataResponse, bucket_name: str,
                               starting_directory: str, secret_name: str, settings: IntegrationConfigClass) -> bool:
    """
    This method retrieves Direct Data files and stores them on a specified S3 bucket. If there are multiple parts to the
    file, this method will merge them and push that completely merged file to the S3 bucket.

    :param settings: The secret manager settings specified
    :param secret_name: The specified secret configuration within the settings file
    :param list_files_response: A Vapil.py response of a List Direct Data Files API call
    :param bucket_name: The name of the S3 bucket where the files are to be pushed too
    :param starting_directory: The starting directory where the Direct Data file is to be stored in teh S3 bucket
    :return: A boolean that signifies whether the operation was successful or not
    """
    vault_client: VaultClient = get_vault_client(settings=settings, secret=secret_name)

    # request: DirectDataRequest = vault_client.new_request(DirectDataRequest)

    s3: BaseClient = boto3.client(service_name="s3")
    # Iterate through the Direct Data file data
    for directDataItem in list_files_response.data:
        # Only execute if there are records present
        if directDataItem.record_count > 0:
            try:
                object_key = f"{starting_directory}"
                request: DirectDataRequest = vault_client.new_request(DirectDataRequest)
                # If there are more than one file parts, then merge the file parts into one valid file and push to
                # the specified S3 bucket. Otherwise just push the entire file to S3.
                if directDataItem.fileparts > 1:
                    multipart_response = s3.create_multipart_upload(Bucket=bucket_name, Key=object_key)
                    upload_id = multipart_response['UploadId']
                    parts = []
                    try:
                        for file_part in directDataItem.filepart_details:
                            file_part_number = file_part.filepart
                            response: VaultResponse = request.download_direct_data_file(file_part.name,
                                                                                        file_part_number)

                            response = s3.upload_part(
                                Bucket=bucket_name,
                                Key=object_key,
                                UploadId=upload_id,
                                PartNumber=file_part_number,
                                Body=response.binary_content
                            )

                            part_info = {'PartNumber': file_part_number, 'ETag': response['ETag']}
                            parts.append(part_info)

                        s3.complete_multipart_upload(
                            Bucket=bucket_name,
                            Key=object_key,
                            UploadId=upload_id,
                            MultipartUpload={'Parts': parts}
                        )
                    except Exception as e:
                        # Abort the multipart upload in case of an error
                        s3.abort_multipart_upload(Bucket=bucket_name, Key=object_key, UploadId=upload_id)
                        log_message(log_level='Error',
                                    message=f'Multi-file upload aborted',
                                    exception=e,
                                    context=None)
                        raise e
                else:
                    try:
                        response: VaultResponse = request.download_direct_data_file(
                            directDataItem.filepart_details[0].name,
                            directDataItem.filepart_details[0].filepart)

                        log_message(log_level='Debug',
                                    message=f'Bucket Name: {bucket_name}, Object key: {object_key}',
                                    context=None)
                        s3.put_object(Bucket=bucket_name, Key=object_key, Body=response.binary_content)


                    except Exception as e:
                        # Abort the multipart upload in case of an error
                        log_message(log_level='Error',
                                    message=f'Could not upload content to S3',
                                    exception=e,
                                    context=None)
                        raise e
            except Exception as e:
                # Abort the multipart upload in case of an error
                log_message(log_level='Error',
                            message=f'Direct Data retrieval aborted',
                            exception=e,
                            context=None)
                raise e
        else:
            log_message(log_level='Info',
                        message=f'No records in the Direct Data extract.',
                        context=None)
            return False
    return True


def list_direct_data_files(start_time: str, stop_time: str, extract_type: str,
                           secret: str, settings: IntegrationConfigClass) -> DirectDataResponse | ResponseClass:
    """
    This method lists the Direct Data files generated by Vault.
    The retrieval is filtered by the provided start and stop times.

    :param secret: The specified secret configuration
    :param start_time: The start time of the Direct Data file generation
    :param stop_time: The stop time of the Direct Data file generation
    :param extract_type: The extract type (incremental or full)
    :return: The Vault API response provided by Vapil.py
    """

    vault_client: VaultClient = get_vault_client(settings=settings, secret=secret)

    try:
        request: DirectDataRequest = vault_client.new_request(DirectDataRequest)
        response: DirectDataResponse = request.retrieve_available_direct_data_files(
            extract_type=ExtractType(extract_type.lower()),
            start_time=start_time, stop_time=stop_time)

        if response.has_errors():
            raise Exception(response.errors[0].message)
    except Exception as e:
        log_message(log_level='Error',
                    message=f'Exception when listing Direct Data files',
                    exception=e,
                    context=None)
        raise e

    return response


def export_documents(doc_version_ids: List[str], secret: str, settings: IntegrationConfigClass) -> JobCreateResponse:
    vault_client = get_vault_client(settings=settings, secret=secret)
    try:
        request_string = []
        for doc_version_id in doc_version_ids:
            split_version_id: list[str] = doc_version_id.split('_')
            doc_id = split_version_id[0]
            major_version = split_version_id[1]
            minor_version = split_version_id[2]

            doc_version_dict = {
                "id": doc_id,
                "major_version_number__v": major_version,
                "minor_version_number__v": minor_version
            }

            request_string.append(doc_version_dict)

        log_message(log_level='Debug',
                    message=f'Vault Client authenticated. Exporting now',
                    context=None)

        doc_request: DocumentRequest = vault_client.new_request(DocumentRequest)
        doc_response: JobCreateResponse = doc_request.export_document_versions(
            request_string=json.dumps(request_string),
            include_source=True,
            include_renditions=False)
        log_message(log_level='Debug',
                    message=f'Job Initiated',
                    context=None)
        return doc_response
    except Exception as e:
        raise e


def download_documents_to_s3(job_id: int, target_path: str, bucket_name: str, secret: str,
                             settings: IntegrationConfigClass) -> List[str]:
    global vault_client
    try:
        vault_client = get_vault_client(settings=settings, secret=secret)

        s3 = boto3.client('s3')

        is_vault_job_finished = False

        log_message(log_level='Info',
                    message=f'Polling status of job {job_id} in Vault',
                    context=None)
        while not is_vault_job_finished:
            document_request: DocumentRequest = vault_client.new_request(DocumentRequest)
            response: DocumentExportResponse = document_request.retrieve_document_export_results(job_id=job_id)

            log_message(log_level='Debug',
                        message=f'Document Export results: {response}',
                        context=None)

            if response.responseStatus == 'SUCCESS':
                for exported_document in response.data:
                    log_message(log_level='Debug',
                                message=f'File Path on Staging Server: {exported_document.file}',
                                context=None)
                    file_staging_request: FileStagingRequest = vault_client.new_request(FileStagingRequest)
                    log_message(log_level='Debug',
                                message=f'File Staging Request: {file_staging_request}',
                                context=None)
                    file_path = str(Path(f'u{exported_document.user_id__v}{exported_document.file}'))
                    file_staging_response: VaultResponse = file_staging_request.download_item_content(
                        item=urllib.parse.quote(file_path))
                    log_message(log_level='Debug',
                                message=f'File Staging results: {file_staging_response}',
                                context=None)
                    s3.put_object(Bucket=bucket_name,
                                  Key=f'{target_path}/{exported_document.id}_{exported_document.major_version_number__v}_{exported_document.minor_version_number__v}',
                                  Body=file_staging_response.binary_content)
                is_vault_job_finished = True
            else:
                log_message(log_level='Debug',
                            message=f'Waiting 12s to check the job status',
                            context=None)
                time.sleep(12)

    except Exception as e:
        raise e
