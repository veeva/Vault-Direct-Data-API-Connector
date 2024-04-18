import psycopg2
import boto3
from .log_message import log_message
from psycopg2 import OperationalError
from botocore.exceptions import ClientError


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
    if 'target_filepath' in job_parameters and job_parameters['target_filepath'] is not None:
        environment_variables.append({'name': 'TARGET_FILEPATH', 'value': job_parameters['target_filepath']})
    if 'continue_processing' in job_parameters and job_parameters['continue_processing'] is not None:
        environment_variables.append({'name': 'CONTINUE_PROCESSING', 'value': job_parameters['continue_processing']})
    if 'start_time' in job_parameters and job_parameters['start_time'] is not None:
        environment_variables.append({'name': 'START_TIME', 'value': job_parameters['start_time']})
    if 'stop_time' in job_parameters and job_parameters['stop_time'] is not None:
        environment_variables.append({'name': 'STOP_TIME', 'value': job_parameters['stop_time']})
    if 'extract_type' in job_parameters and job_parameters['extract_type'] is not None:
        environment_variables.append({'name': 'EXTRACT_TYPE', 'value': job_parameters['extract_type']})

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
    if 'target_filepath' in job_parameters and job_parameters['target_filepath'] is not None:
        container_command.extend(["--target_filepath", job_parameters['target_filepath']])
    if 'continue_processing' in job_parameters and job_parameters['continue_processing'] is not None:
        container_command.extend(["--continue_processing", job_parameters['continue_processing']])
    if 'start_time' in job_parameters and job_parameters['start_time'] is not None:
        container_command.extend(["--start_time", job_parameters['start_time']])
    if 'stop_time' in job_parameters and job_parameters['stop_time'] is not None:
        container_command.extend(["--stop_time", job_parameters['stop_time']])

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


class RedshiftConnection:
    """
    This class is a connector to Redshift.
    This is utilized to execute SQL queries to the specified Redshift database.
    """

    def __init__(self, db_name, hostname, port_number, username, user_password):
        """
        This is initializes the Redshift Connector class with the given paramters that allows the class to connect to an
        active Redshift cluster database

        :param db_name: Name of the database to connect to
        :param hostname: The name of the host of the Redshift cluster
        :param port_number: The port number used to connect.
        :param username: Username of the Redshift user defined on the cluster
        :param user_password: Password for the Redshift user
        """
        self.db_name = db_name
        self.host = hostname
        self.port = port_number
        self.user = username
        self.password = user_password
        self.connected = False
        self.con = self.connect_to_database()

    def connect_to_database(self):
        """
        This method connects the Redshift connector to the database

        :return: If successful, returns a connected Redshift connector
        """
        try:
            con = psycopg2.connect(dbname=self.db_name, host=self.host, port=self.port,
                                   user=self.user, password=self.password)
            con.autocommit = True
            self.connected = True

            return con
        except OperationalError as e:
            log_message(log_level='Error',
                        message=f'Failed to connect to database',
                        exception=e,
                        context=None)

    def run_query(self, query, keep_connection_open: bool):
        """
        This method is a general method to execute Redshift SQL queries to the database.

        :param query: A Redshift SQL query to execute
        :param keep_connection_open: A boolean to signify to keep the connection open or close it.
        """
        try:
            if self.connected is False:
                self.con = self.connect_to_database()
            log_message(log_level='Debug',
                        message=f'Query: {query}',
                        context=None)
            with self.con.cursor() as cursor:
                cursor.execute(query)
            self.con.commit()
            cursor.close()
            if not keep_connection_open:
                self.con.close()
                if self.connected is True:
                    self.connected = False
        except Exception as e:
            raise e

    def table_exists_query_execution(self, query):
        """
        This method executes a Redshift SQL query that specifically determines whether a table exists or not

        :param query: A Redshift SQL query
        :return: Returns ture if the table exists, false if it does not
        """
        if self.connected is False:
            self.con = self.connect_to_database()
        log_message(log_level='Debug',
                    message=f'Query: {query}',
                    context=None)
        with self.con.cursor() as cursor:
            cursor.execute(query)
            result = cursor.fetchall()
        self.con.commit()
        cursor.close()
        self.con.close()
        if self.connected is True:
            self.connected = False
        log_message(log_level='Debug',
                    message=f'table_exists_query_execution: {result[0][0]}',
                    context=None)
        return result[0][0]

    def get_db_column_names(self, query, ordinal_postion_included: bool):
        """
        This method executes a specific Redshift SQL query to list the names of a specified table.

        :param query: A Redshift SQL query
        :param ordinal_postion_included: A boolean to signify whether the column names are ordered by their ordinal position
        :return: The ordered table column names
        """
        if self.connected is False:
            self.con = self.connect_to_database()
        cursor = self.con.cursor()
        log_message(log_level='Debug',
                    message=f'Query: {query}',
                    context=None)
        cursor.execute(query)
        column_names = set()
        column_info = []
        if not ordinal_postion_included:
            column_names = set(col[0].strip('"') for col in cursor.fetchall())
        else:
            column_data = cursor.fetchall()
            column_info = [(col[0].strip('"'), col[1]) for col in column_data]
        self.con.commit()
        cursor.close()
        self.con.close()
        if self.connected is True:
            self.connected = False
        if ordinal_postion_included:
            return column_info
        else:
            return column_names
