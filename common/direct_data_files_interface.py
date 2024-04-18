import gzip
import math
import tarfile
import csv
from io import BytesIO, StringIO
from typing import Dict, List, Any

import boto3
import pandas as pd
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from .api.client.vault_client import VaultClient, AuthenticationType, AuthenticationResponse
from .api.model.response.direct_data_response import DirectDataResponse
from .api.model.response.vault_response import VaultResponse
from .api.request.direct_data_request import DirectDataRequest, ExtractType

from .log_message import log_message
from .integrationConfigClass import IntegrationConfigClass
from .redshift_setup import redshift_table_exists, create_redshift_table, redshift_update_table, load_full_data, \
    delete_data, add_foreign_key_constraint
from .responseClass import ResponseClass


def load_data_into_redshift(schema_name: str, tables_to_load: Dict[str, str], starting_directory: str, s3_bucket: str):
    """
    This method defines the S3 URI of the CSV files and retrieves the CSV headers in the order the columns in the file.

    :param schema_name: Name of the Redshift schema
    :param tables_to_load: A dictionary that maps the table name to the related CSV file
    :param starting_directory: The starting directory where the of where the direct data file is located
    :param s3_bucket: The name of the S3 bucket that the direct data files is stored.
    """
    for table in tables_to_load:
        csv_file = tables_to_load.get(table)
        table_s3_uri = f"s3://{s3_bucket}/{starting_directory}/{csv_file}"

        load_full_data(schema_name=schema_name,
                       table_name=table.split(".")[1].lower(),
                       s3_uri=table_s3_uri,
                       headers=get_csv_headers(s3_bucket, starting_directory, csv_file))


def get_csv_headers(bucket_name, starting_directory, csv_location):
    """
    This method retrieves the headers of the specified CSV in the order they appear in the file

    :param bucket_name: The name of the S3 bucket the CSV is stored in
    :param starting_directory: The directory of where the the source direct data file is located
    :param csv_location: The location of the CSV file within the starting directory
    :return: A string of ordered column headers of the provided CSV files
    """
    log_message(log_level='Info',
                message=f'Retrieving CSV headers for {starting_directory}/{csv_location}',
                context=None)
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket_name, Key=f'{starting_directory}/{csv_location}')
    csv_data = response['Body'].read().decode('utf-8')
    csv_reader = csv.reader(StringIO(csv_data))
    headers = next(csv_reader)

    # Joining headers into a string with ', ' delimiter
    headers_str = ', '.join(headers)

    return headers_str


def verify_redshift_tables(chunk_size: int, bucket_name: str, manifest_path: str, metadata_path: str,
                           starting_directory: str, extract_type: str, metadata_deletes_filepath: str,
                           schema_name: str):
    """
    This method creates the Metadata table if it doesn't already exists.
    It then reads the manifest file and determines whether a table should be created or updated.
    Then it will determine if data for the specified tables in the manifest file needs data loaded or deleted.

    :param chunk_size: The size of how much the manifest data frame should be chunked
    :param bucket_name: Name of the S3 bukcet where the Direct Data files are located
    :param manifest_path: The file path of the manifest file
    :param metadata_path: The file path of the metadata file
    :param starting_directory: The directory of the unzipped Direct Data file
    :param extract_type: The type of Direct Data extract
    :param metadata_deletes_filepath: The file path of the manifest file that contains deletes
    :param schema_name: The name of the schema where the tables should be created or updated
    """
    try:
        manifest_dataframe_itr = pd.read_csv(manifest_path, chunksize=chunk_size)
        metadata_dataframe_itr = pd.read_csv(metadata_path)
        metadata_deletes_dataframe_itr = None
        if metadata_deletes_filepath is not None and len(metadata_deletes_filepath) > 0:
            metadata_deletes_dataframe_itr = pd.read_csv(metadata_deletes_filepath)

    except Exception as e:
        log_message(log_level='Error',
                    message=f'Issue reading Manifest or Metadata CSV',
                    exception=e,
                    context=None)
        raise e

    tables_to_load: Dict[str, str] = {}
    tables_to_delete: Dict[str, str] = {}
    tables_to_verify: List[str] = []
    tables_to_create: List[str] = []
    if extract_type == "full":
        load_metadata_table(schema_name=schema_name, metadata_dataframe=metadata_dataframe_itr)
    # Process each chunk
    for chunk in manifest_dataframe_itr:
        for index, row in chunk.iterrows():
            type = row["type"]
            record_count_not_zero = row["records"] > 0
            full_table_name: str = row["extract"]
            if extract_type == 'full' or record_count_not_zero:
                file_path = row["file"]
                if redshift_table_exists(schema_name=schema_name, table_name=full_table_name.split(".")[1].lower()):
                    if record_count_not_zero:
                        tables_to_verify.append(full_table_name)
                else:
                    tables_to_create.append(full_table_name)
                if type == "updates":
                    if record_count_not_zero:
                        tables_to_load[full_table_name] = file_path
                elif type == 'deletes':
                    tables_to_delete[full_table_name] = file_path

    if len(tables_to_create) > 0:
        log_message(log_level='Info',
                    message=f'Creating new tables',
                    context=None)
        create_new_redshift_tables(table_names=tables_to_create,
                                   schema_name=schema_name,
                                   metadata_dataframe=metadata_dataframe_itr)
    if len(tables_to_verify) > 0:
        log_message(log_level='Info',
                    message=f'Updating existing tables',
                    context=None)
        verify_and_update_existing_tables(table_names=tables_to_verify,
                                          metadata_dataframe=metadata_dataframe_itr,
                                          metadata_deletes_dataframe=metadata_deletes_dataframe_itr,
                                          schema_name=schema_name)
    if len(tables_to_load) > 0:
        log_message(log_level='Info',
                    message=f'Loading data',
                    context=None)
        load_data_into_redshift(schema_name=schema_name,
                                tables_to_load=tables_to_load,
                                starting_directory=starting_directory,
                                s3_bucket=bucket_name)
    if len(tables_to_delete) > 0:
        log_message(log_level='Info',
                    message=f'Deleting data from existing tables',
                    context=None)
        delete_data_from_redshift_table(tables_to_delete, starting_directory, bucket_name)


def load_metadata_table(schema_name: str, metadata_dataframe: pd.DataFrame):
    """
    This method creates or updates the Metadata table. First it checks to see if the table exists.
    If it does then it will update the columns of the table, if not it will create the table in the specified schema

    :param schema_name: The name of the schema where the Metadata table will be created or where it is currently located
    :param metadata_dataframe: The metadata dataframe that documents the current columns of the Metadata table
    """
    columns = metadata_dataframe.columns.tolist()
    column_type_length: dict[str, tuple[str, int]] = {}
    for column in columns:
        column_type_length.update({column: ("string", 1000)})
    table_name = 'metadata'

    if redshift_table_exists(schema_name=schema_name, table_name=table_name):
        redshift_update_table(schema_name=schema_name,
                              table_name=table_name,
                              new_column_names=column_type_length,
                              columns_to_drop=set())
    else:
        create_redshift_table(schema_name=schema_name,
                              table_name=table_name,
                              column_types=create_sql_str(column_type_length, False))


def verify_and_update_existing_tables(table_names: List[str], metadata_dataframe: pd.DataFrame,
                                      metadata_deletes_dataframe: pd.DataFrame, schema_name: str):
    """
    This method verifies that the tables from the manifest files are listed in the metadata file.
    It then retrieves the columns listed in the metadata file, if the metadata_deletes file exists then it lists
    the columns that need to be removed. The method then pass the list of new and
    :param table_names: A list of tables to verify and update
    :param metadata_dataframe: The metadata dataframe to parse
    :param metadata_deletes_dataframe: the metadata_deletes dataframe to parse
    :param schema_name: The name of the schema the tables are located in
    :return:
    """
    columns_to_add = {}
    columns_to_remove = []
    for table in table_names:
        if is_table_in_extract(metadata_dataframe, table):
            # columns: List[str] = metadata_dataframe.loc[table, ["column_name"]]
            columns_to_add: dict[Any, tuple[Any, Any]] = retrieve_table_columns_types_and_lengths(table,
                                                                                                  metadata_dataframe)

        if metadata_deletes_dataframe is not None and is_table_in_extract(metadata_deletes_dataframe, table):
            columns_to_remove = retrieve_columns_to_drop(table, metadata_deletes_dataframe)
        redshift_update_table(schema_name=schema_name,
                              table_name=table.split(".")[1].lower(),
                              new_column_names=columns_to_add,
                              columns_to_drop=columns_to_remove)


def create_new_redshift_tables(table_names: List[str], schema_name: str, metadata_dataframe: pd.DataFrame):
    """
    This method creates new Redshift tables.
    Once the tables are created,
    it alters those same tables to define foreign keys based on the related extract column in the metadata file.

    :param table_names: A list of tables to create
    :param schema_name: The name of the schema where the tables are to be created
    :param metadata_dataframe: The metadata data frame to parse
    """
    relation_alter_table_and_columns: dict[str, dict[str, str]] = {}

    for table in table_names:
        creation_columns = retrieve_table_columns_types_and_lengths(table, metadata_dataframe)

        relation_alter_table_and_columns[table] = retrieve_table_reference_columns(table, metadata_dataframe)

        if not table == 'Picklist.picklist__sys':
            create_redshift_table(schema_name=schema_name,
                                  table_name=table.split(".")[1].lower(),
                                  column_types=create_sql_str(creation_columns, False))
        else:
            create_redshift_table(schema_name=schema_name,
                                  table_name=table.split(".")[1].lower(),
                                  column_types=create_sql_str(creation_columns, True))

    update_reference_columns(table_to_column_dict=relation_alter_table_and_columns,
                             schema_name=schema_name)


def retrieve_columns_to_drop(table: str, metadata_deletes_dataframe: pd.DataFrame):
    """
    This method retrieves a list of columns from the metadata_delete that need to be removed from the table

    :param table: The name of the table that requires the columns to be dropped
    :param metadata_deletes_dataframe: The metadata_deletes dataframe
    :return: A list of columns to drop from the specified table
    """
    try:
        creation_filtered_rows = metadata_deletes_dataframe[metadata_deletes_dataframe['extract'] == table]
        columns_to_drop = creation_filtered_rows['column_name'].values
    except Exception as e:
        raise e

    return set(columns_to_drop)


def retrieve_table_columns_types_and_lengths(table: str, metadata_dataframe: pd.DataFrame):
    """
    This method retrieves the columns, the data type, and, if the column is a string, the length limit. These are defined
    in the metadata dataframe.
    :param table: Name of the table whose columns are being retrieved
    :param metadata_dataframe: The metadata dataframe to parse
    :return: A dictionary mapping the columns to the data type and length
    """
    try:
        creation_filtered_rows = metadata_dataframe[metadata_dataframe['extract'] == table]
        columns_and_types_array = creation_filtered_rows[['column_name', 'type', 'length']].values
    except Exception as e:
        raise e

    return dict(zip(columns_and_types_array[:, 0],
                    zip(columns_and_types_array[:, 1], columns_and_types_array[:, 2])))


def retrieve_table_reference_columns(table: str, metadata_dataframe: pd.DataFrame):
    """
    This method retrieves the related_extract column values in the metadata file and maps them to the specified table.
    :param table: The table with the realted extracts
    :param metadata_dataframe: The metadata dataframe to parse
    :return: A dictionary that maps the table to the list of reference columns
    """
    try:
        alter_filtered_rows = metadata_dataframe[
            (metadata_dataframe['extract'] == table) &
            (metadata_dataframe['related_extract'].notna()) &
            (~metadata_dataframe['related_extract'].isin(
                ['Picklist.picklist__sys', 'Object.media__sys', 'Object.layoutprofile__sys',
                 'Object.tabcollection__sys']))
            ]
        columns_and_references_array = alter_filtered_rows[['column_name', 'related_extract']].values
    except Exception as e:
        raise e

    return dict(zip(columns_and_references_array[:, 0], columns_and_references_array[:, 1]))


def update_reference_columns(table_to_column_dict: dict[str, dict[str, str]], schema_name: str):
    """
    This method loops through the input dictionary and if the reference columns exists,
    a foreign key is added to the table.
    :param table_to_column_dict:
    :param schema_name:
    :return:
    """
    for table, column_and_references in table_to_column_dict.items():
        if bool(column_and_references):
            add_foreign_key_constraint(schema_name=schema_name,
                                       table_name=table.split(".")[1],
                                       columns_and_references=column_and_references)


def delete_data_from_redshift_table(schema_name: str, table_names: Dict[str, str], starting_directory: str,
                                    s3_bucket: str):
    """
    This method iterates through each table ame and determines the S3 bucket location of the CSV file of the data to delete
    from the table.
    :param schema_name: The name of the schema where the table resides
    :param table_names: A dictionary that maps the table to the CSV file location and name
    :param starting_directory: The starting directory of the Direct Data file
    :param s3_bucket: The name of the S3 bucket the direct data files are stored
    """
    try:
        for table, file in table_names.items():
            # table_column_condition = is_table_in_extract(metadata_dataframe, table)
            # if is_table_in_extract(metadata_dataframe, table):
            #     columns_and_types: Dict[str, str] = metadata_dataframe.loc[
            #         metadata_dataframe['extract'] == table, ["column_name", "type"]]
            table_s3_uri = f"s3://{s3_bucket}/{starting_directory}/{file}"
            log_message(log_level='Debug',
                        message=f'Delete file: {table_s3_uri} for table: {table}',
                        context=None)
            delete_data(schema_name=schema_name,
                        table_name=table.split(".")[1].lower(),
                        s3_file_uri=table_s3_uri)
    except Exception as e:
        log_message(log_level='Error',
                    message=f'Error encountered when attempting to delete data',
                    exception=e,
                    context=None)
        raise e


def is_table_in_extract(metadata_dataframe: pd.DataFrame, table_name: str):
    """
    This method determines if a table exists in the metadata file.
    :param metadata_dataframe: The metadata dataframe that needs to be parsed
    :param table_name: The name of the table that needs to be verified
    :return: A boolean that signifies whether the tables is in the extracted Direct Data file
    """
    return table_name in metadata_dataframe['extract'].values


def create_sql_str(fields_dict: dict[str, tuple[str, int]], is_picklist: bool) -> str:
    """
    This method ingests a dictionary that maps a column name to the data type and the length of the string limit (if a string),
    and generates a part of a SQL string that will defines the column of a table when it is created.
    This adds additional constraints for the picklist table.
    :param fields_dict: A dictionary that maps columns to data types and string lengths
    :param is_picklist: A boolean that signifies if the table being created is the picklist table
    :return: A partial SQL string for the column creation
    """
    sql_str = ''

    for k, v in fields_dict.items():
        if isinstance(v, tuple):
            data_type_tuple = v
        else:
            data_type_tuple = tuple[v]

        data_type = data_type_tuple[0].lower()
        data_type_length = data_type_tuple[1]

        if math.isnan(data_type_length):
            data_type_length = 255
        else:
            data_type_length = int(data_type_length)

        if data_type == "id" or (k.lower() == 'id' and data_type == 'string'):
            sql_str += f'"{k}" VARCHAR({data_type_length}) PRIMARY KEY, '
        elif data_type == "datetime":
            sql_str += f'"{k}" TIMESTAMPTZ, '
        elif data_type == "boolean":
            sql_str += f'"{k}" BOOLEAN, '
        elif data_type == "number":
            sql_str += f'"{k}" NUMERIC'
            if k.lower() == "id":
                sql_str += f' PRIMARY KEY, '
            else:
                sql_str += f', '
        elif data_type == "date":
            sql_str += f'"{k}" DATE, '
        else:
            sql_str += f'"{k}" VARCHAR({data_type_length}), '

    if is_picklist:
        sql_str += 'CONSTRAINT picklist_primary_key PRIMARY KEY (object, object_field, picklist_value_name), '
    sql_str = sql_str[:-2]
    return sql_str


def unzip_direct_data_files(bucket_name: str, source_zipped_file_path: str, target_filepath: str) -> bool:
    """
    This method unzips a specified Direct Data file.

    :param bucket_name: S3 bucket name where the zipped file resides
    :param source_zipped_file_path: The file path of the zipped Direct Data file
    :param target_filepath: The target file path to where the unzipped contents are to be placed
    :return: Returns a boolean that signifies if the unzipping execution completed with success or failed
    """
    s3 = boto3.client('s3')
    log_message(log_level='Debug',
                message=f'Unzipping {source_zipped_file_path} to {target_filepath}',
                context=None)

    try:
        # Download the .tar.gz file from S3
        response = s3.get_object(Bucket=bucket_name, Key=source_zipped_file_path)
        tarfile_content = response['Body'].read()
    except ClientError as e:
        log_message(log_level='Error',
                    message=f'There was an issue with retrieving the zipped file content',
                    exception=e,
                    context=None)
        return False

    try:
        # Extract the .tar.gz file in memory
        with tarfile.open(fileobj=gzip.GzipFile(fileobj=BytesIO(tarfile_content), mode='rb'), mode='r') as tar:
            for member in tar.getmembers():
                # Process each file in the .tar.gz archive
                file_content = tar.extractfile(member).read()

                s3_destination_key = f'{target_filepath}{member.name}'

                # Upload the extracted file to S3
                s3.put_object(Bucket=bucket_name, Key=s3_destination_key, Body=file_content)
        return True
    except tarfile.TarError or gzip.BadGzipFile as e:
        if isinstance(tarfile.TarError, e):
            log_message(log_level='Error',
                        message=f'Tar file error',
                        exception=e,
                        context=None)
        else:
            log_message(log_level='Error',
                        message=f'Gzip file error',
                        exception=e,
                        context=None)
        return False


def retrieve_direct_data_files(list_files_response: DirectDataResponse, bucket_name: str,
                               starting_directory: str) -> bool:
    """
    This method retrieves Direct Data files and stores them on a specified S3 bucket. If there are multiple parts to the
    file, this method will merge them and push that completely merged file to the S3 bucket.

    :param list_files_response: A Vapil.py response of a List Direct Data Files API call
    :param bucket_name: The name of the S3 bucket where the files are to be pushed too
    :param starting_directory: The starting directory where the Direct Data file is to be stored in teh S3 bucket
    :return: A boolean that signifies whether the operation was successful or not
    """
    vault_client: VaultClient = get_vault_client()

    # request: DirectDataRequest = vault_client.new_request(DirectDataRequest)

    s3: BaseClient = boto3.client(service_name="s3", region_name='us-east-1')
    for directDataItem in list_files_response.data:
        if directDataItem.size > 0:
            try:
                object_key = f"{starting_directory}"
                request: DirectDataRequest = vault_client.new_request(DirectDataRequest)
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
    return True


def list_direct_data_files(start_time: str, stop_time: str, extract_type: str) -> DirectDataResponse | ResponseClass:
    """
    This method lists the Direct Data files generated by Vault.
    The retrieval is filtered by the provided start and stop times.

    :param start_time: The start time of the Direct Data file generation
    :param stop_time: The stop time of the Direct Data file generation
    :param extract_type: The extract type (incremental or full)
    :return: The Vault API response provided by Vapil.py
    """
    vault_client: VaultClient = get_vault_client()

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


def get_vault_client() -> VaultClient | ResponseClass:
    """
    This generates a Vault Client from Vapil.py given the proper credentials for the target Vault.
    :return: A valid, authenticated Vault Client
    """
    current_region = str(boto3.Session().region_name)
    integration_class: IntegrationConfigClass = IntegrationConfigClass(current_region)

    vault_client: VaultClient = VaultClient(
        vault_client_id='Veeva-Vault-DevSupport-Direct-Data',
        vault_username=integration_class.config.get("vault", "username"),
        vault_password=integration_class.config.get("vault", "password"),
        vault_dns=integration_class.config.get("vault", "dns"),
        authentication_type=AuthenticationType.BASIC)

    try:
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
        return ResponseClass(500, e)
