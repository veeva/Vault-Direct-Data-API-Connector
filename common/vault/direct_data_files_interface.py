import gzip
import io
import json
import math
import sys
import time
import tarfile
import csv
import urllib.parse
from pathlib import Path
from io import BytesIO, StringIO
from typing import Dict, List, Any

import boto3
import pandas as pd
from botocore.exceptions import ClientError
from common.aws_utilities import start_batch_job, upload_large_file
from common.integrationConfigClass import IntegrationConfigClass
from common.aws_utilities import RedshiftConnection

from common.log_message import log_message
from common.redshiftManager import RedshiftManager, update_table_name_that_starts_with_digit


def load_data_into_redshift(schema_name: str, tables_to_load: Dict[str, str], starting_directory: str, s3_bucket: str,
                            extract_docs: bool, settings: IntegrationConfigClass, redshift_manager: RedshiftManager,
                            secret: str):
    """
    This method defines the S3 URI of the CSV files and retrieves the CSV headers in the order the columns in the file.

    :param secret:
    :param redshift_manager:
    :param extract_docs:
    :param settings:
    :param schema_name: Name of the Redshift schema
    :param tables_to_load: A dictionary that maps the table name to the related CSV file
    :param starting_directory: The starting directory where the of where the direct data file is located
    :param s3_bucket: The name of the S3 bucket that the direct data files is stored.
    """
    # List to store asynchronous tasks (if needed)

    for table in tables_to_load:
        csv_file = tables_to_load.get(table)
        table_s3_uri = f"s3://{s3_bucket}/{starting_directory}/{csv_file}"
        table_name = table.split(".")[1]

        if table_name == 'document_version__sys' and extract_docs:
            # Example of starting async task (if needed)
            retrieve_document_source_content_async(s3_bucket, starting_directory, csv_file,
                                                   settings)

        try:
            # Load data into Redshift (assuming this operation is synchronous)
            redshift_manager.load_full_data(schema_name=schema_name,
                                            table_name=table_name.lower(),
                                            s3_uri=table_s3_uri,
                                            headers=get_csv_headers(s3_bucket, starting_directory, csv_file))

        except Exception as e:
            # Handle any exceptions that occur during the execution of load_data_into_redshift
            log_message(log_level='Error',
                        message=f'Error loading {schema_name}.{table_name}',
                        exception=e,
                        context=None)
            raise e


def retrieve_document_source_content_async(bucket_name, starting_directory, csv_location, settings, secret):
    s3 = boto3.client('s3')

    doc_version_ids = retrieve_version_ids(bucket_name, starting_directory,
                                           csv_location)

    doc_version_ids_body_param = json.dumps(doc_version_ids)
    body_param_io = io.StringIO(doc_version_ids_body_param)
    s3_key = f'{starting_directory}/doc_version_ids.txt'
    s3.put_object(Bucket=bucket_name, Key=s3_key, Body=body_param_io.getvalue())
    doc_version_ids_body_param = f's3://{bucket_name}/{s3_key}'

    job_name = settings.config.get(secret, 'job_name')
    job_queue = settings.config.get(secret, 'job_queue')
    job_definition = settings.config.get(secret, 'job_definition')
    job_parameter: Dict[str, str] = {'step': 'extract_docs',
                                     'source_filepath': f'{starting_directory}/source_docs',
                                     'extract_type': 'incremental',
                                     'doc_version_ids': f'{doc_version_ids_body_param}'}

    batch_job_response = start_batch_job(job_name=f'{job_name}-export', job_queue=job_queue,
                                         job_definition=job_definition,
                                         job_parameters=job_parameter)
    if 'jobId' in batch_job_response:
        log_message(log_level='Info',
                    message=f"Job started successfully with Job ID: {batch_job_response['jobId']}",
                    context=None)
    else:
        log_message(log_level='Error',
                    message=f'Failed to start job: {batch_job_response}',
                    context=None)


def batch_list(data, batch_size):
    """Yield successive n-sized chunks from a list."""
    for i in range(0, len(data), batch_size):
        yield data[i:i + batch_size]


def retrieve_version_ids(bucket_name, starting_directory, csv_location):
    log_message(log_level='Info',
                message=f'Retrieving document version IDs',
                context=None)
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket_name, Key=f'{starting_directory}/{csv_location}')
    csv_data = response['Body'].read().decode('utf-8')
    csv_reader = csv.reader(StringIO(csv_data))
    headers = next(csv_reader)
    column_values = []

    column_name = 'version_id'
    try:
        column_index = headers.index(column_name)
    except ValueError:
        raise ValueError(f"Column '{column_name}' not found in CSV headers: {headers}")

    for row in csv_reader:
        if len(row) > column_index:
            column_values.append(row[column_index])
        else:
            column_values.append(None)

    return column_values


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

    try:
        with io.TextIOWrapper(response['Body'], encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            headers = next(csv_reader)  # Read the first line containing headers

        updated_headers = [update_table_name_that_starts_with_digit(header) for header in headers]
        headers_str = ', '.join(updated_headers)

        return headers_str
    except csv.Error as e:
        log_message(log_level='Error',
                    message=f'Error reading CSV file: {e}',
                    exception=e,
                    context=None)
        return None
    except StopIteration:
        log_message(log_level='Error',
                    message='CSV file appears to be empty or corrupted',
                    context=None)
        return None


def verify_redshift_tables(chunk_size: int, bucket_name: str, manifest_path: str, metadata_path: str,
                           starting_directory: str, extract_type: str, metadata_deletes_filepath: str,
                           schema_name: str, extract_docs: bool, settings: IntegrationConfigClass, secret: str):
    """
    This method creates the Metadata table if it doesn't already exists.
    It then reads the manifest file and determines whether a table should be created or updated.
    Then it will determine if data for the specified tables in the manifest file needs data loaded or deleted.

    :param secret: The specified configured secret within the setting file
    :param settings: The Secrets Manager settings file
    :param extract_docs: Extract document source content or not.
    :param chunk_size: The size of how much the manifest data frame should be chunked
    :param bucket_name: Name of the S3 bucket where the Direct Data files are located
    :param manifest_path: The file path of the manifest file
    :param metadata_path: The file path of the metadata file
    :param starting_directory: The directory of the unzipped Direct Data file
    :param extract_type: The type of Direct Data extract
    :param metadata_deletes_filepath: The file path of the manifest file that contains deletes
    :param schema_name: The name of the schema where the tables should be created or updated
    """

    log_message(log_level='Info',
                message=f'The metadata: {metadata_path} and manifest: {manifest_path}',
                exception=None,
                context=None)

    try:

        # This class handles forming queries and establishing redshift connections
        redshift_manager: RedshiftManager = RedshiftManager(settings=settings, secret=secret)

        # Initialize dataframes that will allow the parsing of the appropriate manifest and metadata CSV files.
        manifest_dataframe_itr = pd.read_csv(manifest_path, chunksize=chunk_size)
        metadata_dataframe_itr = pd.read_csv(metadata_path)
        metadata_deletes_dataframe_itr = None

        # Current bug in Direct Data extract where the document_number__v field is defined as a number instead of a
        # string for document_version__sys elements. This makes an update to the metadata dataframe to reconcile that
        # bug.
        metadata_dataframe_itr.loc[
            (metadata_dataframe_itr['extract'] == 'Document.document_version__sys') &
            (metadata_dataframe_itr['column_name'] == 'document_number__v'),
            ['type', 'length']
        ] = ['String', 255]

        # Current bug in Direct Data extract where the description__sys field is defined as allowing a length of 128
        # characters. Vault allows 255 characters for that field. This makes an update to the metadata dataframe to
        # reconcile that bug.
        metadata_dataframe_itr.loc[
            (metadata_dataframe_itr['extract'] == 'Object.security_policy__sys') &
            (metadata_dataframe_itr['column_name'] == 'description__sys'),
            ['type', 'length']
        ] = ['String', 255]

        metadata_dataframe_itr.loc[
            (metadata_dataframe_itr['extract'] == 'Object.edl_item__v') &
            (metadata_dataframe_itr['column_name'] == 'progress_icon__v'),
            ['type', 'length']
        ] = ['String', 32000]

        if metadata_deletes_filepath is not None and len(metadata_deletes_filepath) > 0:
            log_message(log_level='Info',
                        message=f'The metadata_deletes file exists',
                        exception=None,
                        context=None)
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
    # If this a full extract, the assumption is that the database is being established and the metadata table needs
    # to be created first.
    if extract_type == "full":
        load_metadata_table(schema_name=schema_name,
                            metadata_dataframe=metadata_dataframe_itr,
                            redshift_manager=redshift_manager,
                            settings=settings)
    # Process each chunked data from the manifest dataframe
    for chunk in manifest_dataframe_itr:
        for index, row in chunk.iterrows():
            type = row["type"]
            # Only process elements that have records present
            record_count_not_zero = row["records"] > 0
            full_table_name: str = row["extract"]
            if extract_type == 'full' or record_count_not_zero:
                file_path = row["file"]
                table_name = full_table_name.split(".")[1].lower()
                if table_name[0].isdigit():
                    table_name = 'n_' + table_name
                if not (extract_type == "full" and table_name == 'metadata'):
                    # Check to see if the schema and table exists.
                    if redshift_manager.redshift_table_exists(schema_name=schema_name, table_name=table_name,
                                                              settings=settings):
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
                                   metadata_dataframe=metadata_dataframe_itr,
                                   redshift_manager=redshift_manager)
    if len(tables_to_verify) > 0:
        log_message(log_level='Info',
                    message=f'Updating existing tables',
                    context=None)
        verify_and_update_existing_tables(table_names=tables_to_verify,
                                          metadata_dataframe=metadata_dataframe_itr,
                                          metadata_deletes_dataframe=metadata_deletes_dataframe_itr,
                                          schema_name=schema_name,
                                          redshift_manager=redshift_manager)
    if len(tables_to_load) > 0:
        log_message(log_level='Info',
                    message=f'Loading data',
                    context=None)
        load_data_into_redshift(schema_name=schema_name,
                                tables_to_load=tables_to_load,
                                starting_directory=starting_directory,
                                s3_bucket=bucket_name,
                                extract_docs=extract_docs,
                                settings=settings,
                                redshift_manager=redshift_manager,
                                secret=secret)
    if len(tables_to_delete) > 0:
        log_message(log_level='Info',
                    message=f'Deleting data from existing tables',
                    context=None)
        delete_data_from_redshift_table(schema_name=schema_name,
                                        table_names=tables_to_delete,
                                        starting_directory=starting_directory,
                                        s3_bucket=bucket_name,
                                        redshift_manager=redshift_manager)


def load_metadata_table(schema_name: str, metadata_dataframe: pd.DataFrame, redshift_manager: RedshiftManager,
                        settings: IntegrationConfigClass):
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

    if redshift_manager.redshift_table_exists(schema_name=schema_name, table_name=table_name, settings=settings):
        redshift_manager.redshift_update_table(schema_name=schema_name,
                                               table_name=table_name,
                                               new_column_names=column_type_length,
                                               columns_to_drop=set())
    else:
        redshift_manager.create_redshift_table(schema_name=schema_name,
                                               table_name=table_name,
                                               column_types=create_sql_str(column_type_length, False))


def verify_and_update_existing_tables(table_names: List[str], metadata_dataframe: pd.DataFrame,
                                      metadata_deletes_dataframe: pd.DataFrame, schema_name: str,
                                      redshift_manager: RedshiftManager):
    """
    This method verifies that the tables from the manifest files are listed in the metadata file.
    It then retrieves the columns listed in the metadata file, if the metadata_deletes file exists then it lists
    the columns that need to be removed. The method then pass the list of new and
    :param redshift_manager:
    :param table_names: A list of tables to verify and update
    :param metadata_dataframe: The metadata dataframe to parse
    :param metadata_deletes_dataframe: the metadata_deletes dataframe to parse
    :param schema_name: The name of the schema the tables are located in
    :return:
    """
    log_message(log_level='Debug',
                message=f'Start of updating the tables',
                exception=None,
                context=None)
    columns_to_add = {}
    columns_to_remove = []
    tables_dropped = []

    try:
        log_message(log_level='Debug',
                    message=f'Checking if metadata_deletes exists',
                    exception=None,
                    context=None)
        if metadata_deletes_dataframe is not None:
            tables_dropped = drop_table_for_deleted_objects(schema_name, table_names, metadata_deletes_dataframe)
            log_message(log_level='Debug',
                        message=f'Tables dropped: {tables_dropped}',
                        exception=None,
                        context=None)
        log_message(log_level='Debug',
                    message=f'Iterating through all the tables',
                    exception=None,
                    context=None)
        for table in table_names:
            log_message(log_level='Debug',
                        message=f'Checking if {table} is in the extract',
                        exception=None,
                        context=None)
            if is_table_in_extract(metadata_dataframe, table):
                # columns: List[str] = metadata_dataframe.loc[table, ["column_name"]]
                columns_to_add: dict[Any, tuple[Any, Any]] = retrieve_table_columns_types_and_lengths(table,
                                                                                                      metadata_dataframe)
            log_message(log_level='Debug',
                        message=f'Checking if metadata_deletes exists again',
                        exception=None,
                        context=None)
            if metadata_deletes_dataframe is not None:
                if len(tables_dropped) == 0 or table not in tables_dropped:
                    columns_to_remove = retrieve_columns_to_drop(table, schema_name, metadata_deletes_dataframe)

            if columns_to_remove:
                log_message(log_level='Debug',
                            message=f'Updating the tables',
                            exception=None,
                            context=None)
                redshift_manager.redshift_update_table(schema_name=schema_name,
                                                       table_name=table.split(".")[1].lower(),
                                                       new_column_names=columns_to_add,
                                                       columns_to_drop=set(columns_to_remove))
    except Exception as e:
        log_message(log_level='Error',
                    message=f'There was an issue updating the tables.',
                    exception=e,
                    context=None)
        raise e


def create_new_redshift_tables(table_names: List[str], schema_name: str, metadata_dataframe: pd.DataFrame,
                               redshift_manager: RedshiftManager):
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
            redshift_manager.create_redshift_table(schema_name=schema_name,
                                                   table_name=table.split(".")[1].lower(),
                                                   column_types=create_sql_str(creation_columns, False))
        else:
            redshift_manager.create_redshift_table(schema_name=schema_name,
                                                   table_name=table.split(".")[1].lower(),
                                                   column_types=create_sql_str(creation_columns, True))

    update_reference_columns(table_to_column_dict=relation_alter_table_and_columns,
                             schema_name=schema_name, redshift_manager=redshift_manager)


def drop_table_for_deleted_objects(schema_name: str, metadata_deletes_dataframe: pd.DataFrame,
                                   redshift_manager: RedshiftManager):
    try:
        filtered_rows = metadata_deletes_dataframe[metadata_deletes_dataframe['column_name'] == 'id']

        unique_tables_to_drop = filtered_rows['extract'].unique()

        processed_tables = set()

        for table_to_drop in unique_tables_to_drop:
            if table_to_drop not in processed_tables:
                redshift_manager.drop_table(schema_name, table_to_drop.split(".")[1])
                processed_tables.add(table_to_drop)

        return list(processed_tables)


    except Exception as e:
        log_message(log_level='Error',
                    message=f'Error when searching for tables to drop',
                    exception=e,
                    context=None)
        raise e


def retrieve_columns_to_drop(table: str, metadata_deletes_dataframe: pd.DataFrame):
    """
    This method retrieves a list of columns from the metadata_delete that need to be removed from the table

    :param schema_name: The name of the database schema
    :param table: The name of the table that requires the columns to be dropped
    :param metadata_deletes_dataframe: The metadata_deletes dataframe
    :return: A list of columns to drop from the specified table
    """
    try:
        if is_table_in_extract(metadata_deletes_dataframe, table):
            creation_filtered_rows = metadata_deletes_dataframe[metadata_deletes_dataframe['extract'] == table]
            return creation_filtered_rows['column_name'].values
    except Exception as e:
        raise e


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


def update_reference_columns(table_to_column_dict: dict[str, dict[str, str]], schema_name: str,
                             redshift_manager: RedshiftManager):
    """
    This method loops through the input dictionary and if the reference columns exists,
    a foreign key is added to the table.
    :param redshift_manager:
    :param table_to_column_dict:
    :param schema_name:
    :return:
    """
    for table, column_and_references in table_to_column_dict.items():
        if bool(column_and_references):
            redshift_manager.add_foreign_key_constraint(schema_name=schema_name,
                                                        table_name=table.split(".")[1],
                                                        columns_and_references=column_and_references)


def delete_data_from_redshift_table(schema_name: str, table_names: Dict[str, str], starting_directory: str,
                                    s3_bucket: str, redshift_manager: RedshiftManager):
    """
    This method iterates through each table ame and determines the S3 bucket location of the CSV file of the data to delete
    from the table.
    :param redshift_manager:
    :param schema_name: The name of the schema where the table resides
    :param table_names: A dictionary that maps the table to the CSV file location and name
    :param starting_directory: The starting directory of the Direct Data file
    :param s3_bucket: The name of the S3 bucket the direct data files are stored
    """
    try:
        for table, file in table_names.items():
            if redshift_manager.redshift_table_exists(schema_name=schema_name, table_name=table.split(".")[1].lower()):
                table_s3_uri = f"s3://{s3_bucket}/{starting_directory}/{file}"
                log_message(log_level='Debug',
                            message=f'Delete file: {table_s3_uri} for table: {table}',
                            context=None)
                redshift_manager.delete_data(schema_name=schema_name,
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

        print(f'Data Type Length: {data_type_length}')

        if math.isnan(data_type_length) or not data_type_length or data_type_length == "":
            data_type_length = 32000
        else:
            data_type_length = int(data_type_length) * 2

        k = update_table_name_that_starts_with_digit(k)

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
                if len(file_content) > 5 * 1024 * 1024:
                    upload_large_file(s3, bucket_name, s3_destination_key, file_content)
                else:
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
