import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

import psycopg2
import boto3
from .log_message import log_message
from psycopg2 import OperationalError
from botocore.exceptions import ClientError

thread_local = threading.local()


def invoke_lambda(function_name: str, payload):
    """
    This function invokes a specified AWS Lambda function

    :param function_name: The name of the lambda function to invoke
    :param payload: The input parameters for invoking the lambda
    :return: The AWS API Lambda invocation response
    """
    lambda_client = boto3.client('lambda')

    try:
        response = lambda_client.invoke(FunctionName=function_name,
                                        InvocationType='Event',
                                        Payload=payload)

        return response
    except ClientError as e:
        log_message(log_level='Error',
                    message=f'Failed to invoke lambda',
                    exception=e,
                    context=None)
        raise e


def start_batch_job(job_name: str, job_queue: str, job_definition: str, job_parameters):
    """
    This function submits a specified AWS Batch job

    :param job_name: Name of the AWS Batch Job
    :param job_queue: Name of the AWS Batch Job Queue
    :param job_definition: Name of the AWS Batch Job Definition
    :param job_parameters: Input parameters for the AWS Batch Job
    :return: The AWS API Batch job submission response
    """
    batch_client = boto3.client("batch")
    try:
        response = batch_client.submit_job(
            jobName=job_name,
            jobQueue=job_queue,
            jobDefinition=job_definition,
            containerOverrides={
                'environment': generate_enviroment_variables(job_parameters),
                'command': generate_command_overrides(job_parameters)
            }
        )

        return response
    except ClientError as e:
        log_message(log_level='Error',
                    message=f'Failed to submit batch job',
                    exception=e,
                    context=None)
        raise e


def generate_enviroment_variables(job_parameters):
    """
    This method appends environment variables to AWS Batch job parameters provided

    :param job_parameters: The AWS Batch input parameters
    :return: the job parameters with environment variables appended
    """
    environment_variables = []
    if 'step' in job_parameters and job_parameters['step'] is not None:
        environment_variables.append({'name': 'STEP', 'value': job_parameters['step']})
    if 'source_filepath' in job_parameters and job_parameters['source_filepath'] is not None:
        environment_variables.append({'name': 'SOURCE_FILEPATH', 'value': job_parameters['source_filepath']})
    if 'target_directory' in job_parameters and job_parameters['target_directory'] is not None:
        environment_variables.append({'name': 'TARGET_DIRECTORY', 'value': job_parameters['target_directory']})
    if 'continue_processing' in job_parameters and job_parameters['continue_processing'] is not None:
        environment_variables.append({'name': 'CONTINUE_PROCESSING', 'value': job_parameters['continue_processing']})
    if 'start_time' in job_parameters and job_parameters['start_time'] is not None:
        environment_variables.append({'name': 'START_TIME', 'value': job_parameters['start_time']})
    if 'stop_time' in job_parameters and job_parameters['stop_time'] is not None:
        environment_variables.append({'name': 'STOP_TIME', 'value': job_parameters['stop_time']})
    if 'extract_type' in job_parameters and job_parameters['extract_type'] is not None:
        environment_variables.append({'name': 'EXTRACT_TYPE', 'value': job_parameters['extract_type']})
    if 'doc_version_ids' in job_parameters and job_parameters['doc_version_ids'] is not None:
        environment_variables.append({'name': 'DOC_VERSION_IDS', 'value': job_parameters['doc_version_ids']})
    if 'extract_source_content' in job_parameters and job_parameters['extract_source_content'] is not None:
        environment_variables.append(
            {'name': 'EXTRACT_SOURCE_CONTENT', 'value': job_parameters['extract_source_content']})
    if 'secret_name' in job_parameters and job_parameters['secret_name'] is not None:
        environment_variables.append({'name': 'SECRET_NAME', 'value': job_parameters['secret_name']})
    if 'secret' in job_parameters and job_parameters['secret'] is not None:
        environment_variables.append({'name': 'SECRET', 'value': job_parameters['secret']})

    return environment_variables


def generate_command_overrides(job_parameters):
    """
    This method appends command override statements to AWS Batch job parameters provided

    :param job_parameters: The AWS Batch input parameters
    :return: the job parameters with command overrides appended
    """
    container_command = [
        "python",
        "run.py"
    ]

    if 'step' in job_parameters and job_parameters['step'] is not None:
        container_command.extend(["--step", job_parameters['step']])
    if 'source_filepath' in job_parameters and job_parameters['source_filepath'] is not None:
        container_command.extend(["--source_filepath", job_parameters['source_filepath']])
    if 'target_directory' in job_parameters and job_parameters['target_directory'] is not None:
        container_command.extend(["--target_directory", job_parameters['target_directory']])
    if 'continue_processing' in job_parameters and job_parameters['continue_processing'] is not None:
        container_command.extend(["--continue_processing", job_parameters['continue_processing']])
    if 'start_time' in job_parameters and job_parameters['start_time'] is not None:
        container_command.extend(["--start_time", job_parameters['start_time']])
    if 'stop_time' in job_parameters and job_parameters['stop_time'] is not None:
        container_command.extend(["--stop_time", job_parameters['stop_time']])
    if 'extract_type' in job_parameters and job_parameters['extract_type'] is not None:
        container_command.extend(["--extract_type", job_parameters['extract_type']])
    if 'doc_version_ids' in job_parameters and job_parameters['doc_version_ids'] is not None:
        container_command.extend(["--doc_version_ids", job_parameters['doc_version_ids']])
    if 'secret_name' in job_parameters and job_parameters['secret_name'] is not None:
        container_command.extend(["--secret_name", job_parameters['secret_name']])
    if 'secret' in job_parameters and job_parameters['secret'] is not None:
        container_command.extend(["--secret", job_parameters['secret']])

    return container_command


def get_batch_region():
    batch_client = boto3.client('batch')
    response = batch_client.describe_compute_environments()
    compute_environments = response['computeEnvironments']

    if compute_environments:
        compute_environment_arn = compute_environments[0]['computeEnvironmentArn']
        current_region = compute_environment_arn.split(':')[3]
        print("Batch invoked in region:", current_region)
        return current_region
    else:
        print("No compute environments found.")
        raise Exception("No compute environments found.")


def upload_large_file(s3, bucket_name, key, file_content):
    try:
        # Initiate multipart upload
        multipart_upload = s3.create_multipart_upload(Bucket=bucket_name, Key=key)
        upload_id = multipart_upload['UploadId']

        # Upload parts
        parts = []
        part_size = 5 * 1024 * 1024  # 5 MB
        for i in range(0, len(file_content), part_size):
            part_num = len(parts) + 1
            part_data = file_content[i:i + part_size]
            part = s3.upload_part(
                Bucket=bucket_name,
                Key=key,
                PartNumber=part_num,
                UploadId=upload_id,
                Body=part_data
            )
            parts.append({'PartNumber': part_num, 'ETag': part['ETag']})

        # Complete multipart upload
        s3.complete_multipart_upload(
            Bucket=bucket_name,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )
    except ClientError as e:
        s3.abort_multipart_upload(Bucket=bucket_name, Key=key, UploadId=upload_id)
        log_message(log_level='Error',
                    message=f'Multipart upload failed',
                    exception=e,
                    context=None)
        raise e


def get_s3_path(filename, s3_bucket, subfolder):
    """

    :param filename: The name of the file to locate
    :param s3_bucket: The name of the S3 bucket
    :param subfolder: The directory the file is located
    :param file_type: full, updates or deletes. Depending on these choices, the file key is searched.
    :return:
    """
    try:
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(s3_bucket)
        prefix = f"{subfolder}/"
        for obj in bucket.objects.filter(Prefix=prefix):
            if filename in obj.key:
                return f"s3://{s3_bucket}/{obj.key}"
        log_message(log_level='Info',
                    message=f'For {filename}, s3 file not found',
                    exception=None, context=None)
    except Exception as e:
        raise e
    return None


def retrieve_doc_version_ids_from_s3(file_path: str) -> list[str]:
    s3 = boto3.client('s3')

    # Check if file_path is a filepath in S3
    if file_path.startswith("s3://"):
        # It's an S3 path, so parse the bucket and key
        s3_path_parts = file_path[5:].split("/", 1)
        bucket_name = s3_path_parts[0]
        key = s3_path_parts[1]

        try:
            # Download the file from S3
            response = s3.get_object(Bucket=bucket_name, Key=key)
            file_content = response['Body'].read().decode('utf-8')

            # Attempt to parse the file content as JSON
            doc_version_ids_list = json.loads(file_content)
            log_message(log_level='Debug',
                        message=f'Amount of documents to be extracted in total: {len(doc_version_ids_list)}',
                        context=None)
            if isinstance(doc_version_ids_list, list):
                # Get the first 10,000 document version IDs
                batch_size = 10000
                first_batch = doc_version_ids_list[:batch_size]
                log_message(log_level='Debug',
                            message=f'Amount of batched documents to be extracted {len(first_batch)}',
                            context=None)
                remaining_batch = doc_version_ids_list[batch_size:]
                log_message(log_level='Debug',
                            message=f'Amount of remaining documents to be extracted {len(remaining_batch)}',
                            context=None)

                if remaining_batch:
                    log_message(log_level='Debug',
                                message=f'Updating the file: {key}',
                                context=None)
                    # If there are remaining IDs, update the file on S3
                    remaining_content = json.dumps(remaining_batch)
                    s3.put_object(Bucket=bucket_name, Key=key, Body=remaining_content)
                else:
                    log_message(log_level='Debug',
                                message=f'Deleting the file: {key}',
                                context=None)
                    # If no IDs are left, delete the file from S3
                    s3.delete_object(Bucket=bucket_name, Key=key)
                    log_message(log_level='Info',
                                message=f'File {key} deleted successfully',
                                context=None)

                return first_batch
            else:
                raise ValueError("The content is not a valid list of document version IDs")
        except (s3.exceptions.NoSuchKey, json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Error processing S3 file {file_path}: {e}")
    else:
        try:
            # If it's not an S3 path, assume it's a JSON string
            doc_version_ids_list = json.loads(file_path)
            if isinstance(doc_version_ids_list, list):
                return doc_version_ids_list
            else:
                raise ValueError("The string is not a valid list of document version IDs")
        except json.JSONDecodeError:
            raise ValueError(f"The parameter is neither a valid S3 path nor a valid JSON array: {file_path}")


def check_file_exists_s3(bucket_name: str, file_key: str) -> bool:
    """
    Check if a file exists in an S3 bucket.

    :param bucket_name: The name of the S3 bucket.
    :param file_key: The key (path) to the file in the S3 bucket.
    :return: True if the file exists, False otherwise.
    """
    s3 = boto3.client('s3')

    try:
        s3.head_object(Bucket=bucket_name, Key=file_key)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            raise e


def execute_in_threads(task: Callable, args_list: Iterable[tuple]) -> None:
    """
    General method to execute tasks in parallel using threading.
    """
    with ThreadPoolExecutor() as executor:
        # Submit all tasks to the executor
        futures = {executor.submit(task, *args): args for args in args_list}

        # Wait for each future to complete and handle exceptions
        for future in as_completed(futures):
            task_args = futures[future]
            try:
                result = future.result()  # Will raise any exceptions that occurred during task execution
            except Exception as e:
                log_message(log_level='Error',
                            message=f'Exception occurred for args: {task_args}',
                            exception=e,
                            context=None)


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
        if data_type_length is None or data_type_length == "" or (isinstance(data_type_length, float) and math.isnan(data_type_length)):
            data_type_length = 64000
        else:
            data_type_length = int(data_type_length)
            if data_type_length > 32000:
                data_type_length = 64000
            else:
                data_type_length *= 2

        k = update_table_name_that_starts_with_digit(k)

        if data_type == "id" or (k.lower() == 'id' and data_type == 'string'):
            sql_str += f'"{k}" VARCHAR({data_type_length}) PRIMARY KEY, '
        elif data_type == "datetime" or data_type == "timestamp with time zone":
            sql_str += f'"{k}" TIMESTAMPTZ, '
        elif data_type == "boolean":
            sql_str += f'"{k}" BOOLEAN, '
        elif data_type == "number" or data_type == "numeric":
            sql_str += f'"{k}" NUMERIC'
            if k.lower() == "id":
                sql_str += f' PRIMARY KEY, '
            else:
                sql_str += f', '
        elif data_type == "date":
            sql_str += f'"{k}" DATE, '
        else:
            # This logic to handle icon fields.
            if data_type == 'string' and data_type_length == 2:
                data_type_length = 64000
            sql_str += f'"{k}" VARCHAR({data_type_length}), '

    if is_picklist:
        sql_str += 'CONSTRAINT picklist_primary_key PRIMARY KEY (object, object_field, picklist_value_name), '
    sql_str = sql_str[:-2]
    return sql_str


def update_table_name_that_starts_with_digit(table_name: str) -> str:
    """
    This method handles reconciling Vault objects that begin with a number and appending a 'n_' so that Redshift will
    accept the naming convention
    :param table_name: The name of the table that needs to be update
    :return: The updated table name
    """
    if table_name[0].isdigit():
        return f'n_{table_name}'
    else:
        return table_name


class RedshiftConnection:
    def __init__(self, db_name, hostname, port_number, username, user_password):
        """
        This initializes the Redshift Connector class with the given parameters that allow the class to connect to an
        active Redshift cluster database.
        """
        self.db_name = db_name
        self.host = hostname
        self.port = port_number
        self.user = username
        self.password = user_password
        self.connected = False

    def connect_to_database(self):
        """
        This method connects the Redshift connector to the database for each thread.
        If a connection already exists for the thread, it uses that connection.
        """
        try:
            # Check if a connection exists in thread-local storage
            if not hasattr(thread_local, 'conn') or thread_local.conn is None:
                # If no connection exists, create a new one
                con = psycopg2.connect(
                    dbname=self.db_name,
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password
                )
                con.autocommit = True
                thread_local.conn = con  # Store the connection in thread-local storage
                self.connected = True

            # Return the connection from thread-local storage
            return thread_local.conn

        except OperationalError as e:
            log_message(
                log_level='Error',
                message='Failed to connect to database',
                exception=e,
                context=None
            )
            raise e

    def run_query(self, query, keep_connection_open: bool):
        """
        Executes Redshift SQL queries using the thread-local connection.
        """
        try:
            con = self.connect_to_database()  # Each thread gets its own connection
            log_message(log_level='Debug', message=f'Query: {query}', context=None)
            with con.cursor() as cursor:
                cursor.execute(query)
            con.commit()
            cursor.close()
            if not keep_connection_open:
                con.close()
                thread_local.conn = None  # Reset thread-local connection
                self.connected = False
        except Exception as e:
            log_message(log_level='Error',
                        message=f'Error executing query: {query}',
                        exception=e,
                        context=None)
            raise e

    def table_exists_query_execution(self, query):
        """
        This method executes a Redshift SQL query that specifically determines whether a table exists or not.

        :param query: A Redshift SQL query
        :return: Returns true if the table exists, false if it does not
        """
        con = self.connect_to_database()  # Use the thread-local connection
        log_message(log_level='Debug',
                    message=f'Query: {query}',
                    context=None)

        with con.cursor() as cursor:
            cursor.execute(query)
            result = cursor.fetchall()

        con.commit()
        cursor.close()

        # Optionally close the connection if needed
        # If you are using thread-local storage, closing it might not be necessary until after the task is complete
        if not self.connected:
            con.close()
            thread_local.conn = None  # Reset the thread-local connection
            self.connected = False

        log_message(log_level='Debug',
                    message=f'table_exists_query_execution: {result[0][0]}',
                    context=None)
        return result[0][0]

    def get_db_column_names(self, query, ordinal_postion_included: bool) -> dict:
        """
        This method executes a specific Redshift SQL query to list the names of a specified table.

        :param query: A Redshift SQL query
        :param ordinal_postion_included: A boolean to signify whether the column names are ordered by their ordinal position
        :return: The ordered table column names
        """
        con = self.connect_to_database()  # Use the thread-local connection
        cursor = con.cursor()
        log_message(log_level='Debug',
                    message=f'Query: {query}',
                    context=None)

        cursor.execute(query)
        fields_dict = {}

        column_data = cursor.fetchall()

        # Parse column names and lengths
        for column_name, data_type, char_length in column_data:
            # Ensure char_length is handled appropriately
            if char_length is None:
                char_length = math.nan
            else:
                char_length = int(char_length)
            fields_dict[column_name] = (data_type, char_length)

        # Close cursor and connection
        con.commit()
        cursor.close()
        if not self.connected:
            con.close()
            thread_local.conn = None  # Reset the thread-local connection
            self.connected = False

        return fields_dict
