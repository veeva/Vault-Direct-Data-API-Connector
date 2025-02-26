import math
from typing import Any

from .integrationConfigClass import IntegrationConfigClass
from .aws_utilities import RedshiftConnection, create_sql_str, update_table_name_that_starts_with_digit
from .log_message import log_message


class RedshiftManager:
    def __init__(self, settings: IntegrationConfigClass, secret):
        self.host = settings.config.get(secret, 'redshift_host')
        self.dbname = settings.config.get(secret, 'redshift_dbname')
        self.user = settings.config.get(secret, 'redshift_user')
        self.password = settings.config.get(secret, 'redshift_password')
        self.port = settings.config.get(secret, 'redshift_port')
        self.iam_role = settings.config.get(secret, 'redshift_iam_redshift_s3_read')
        self.redshift_conn = self.get_redshift_connection()

    def get_redshift_connection(self) -> RedshiftConnection:

        return RedshiftConnection(
            db_name=self.dbname,
            hostname=self.host,
            port_number=self.port,
            username=self.user,
            user_password=self.password
        )

    def redshift_table_exists(self, schema_name: str, table_name: str, settings: IntegrationConfigClass) -> bool:
        """
        This method queries a Redshift database and determines if a specified schema and table exists. If the schema does not
        exist, the schema will be created
        :param settings: Specified Secrets Manager settings file
        :param schema_name: The name of the schema where the tables exist
        :param table_name: The name of the table that is to be verified
        :return: A boolean that signifies whether the table exists or not
        """

        table_name = update_table_name_that_starts_with_digit(table_name)

        query = f"""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.schemata
                        WHERE 
                        schema_name = '{schema_name}'
                    )
                """
        try:
            schema_exists_result = self.redshift_conn.table_exists_query_execution(query)
            if schema_exists_result is False:
                log_message(log_level='Debug',
                            message=f'{schema_name} does not exist. Creating new schema',
                            context=None)
                create_schema_query = f"""
                            CREATE SCHEMA {schema_name};
                        """
                self.redshift_conn.run_query(create_schema_query, False)
                return False

            elif schema_exists_result is True:
                log_message(log_level='Debug',
                            message=f'{schema_name} exists. Creating {table_name} in {schema_name} schema',
                            context=None)
                table_exists_query = f"""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE
                        table_catalog = '{self.dbname}' 
                        AND table_schema = '{schema_name}'
                        AND table_name = '{table_name}'
                    )
                """
                try:
                    table_exists_result = self.redshift_conn.table_exists_query_execution(table_exists_query)
                    return table_exists_result
                except Exception as e:
                    log_message(log_level='Error',
                                message=f'Error checking if table {self.dbname}.{schema_name}.{table_name} exists',
                                exception=e,
                                context=None)
                    raise e
        except Exception as e:
            log_message(log_level='Error',
                        message=f'Error checking if table {self.dbname}.{schema_name}{table_name} exists',
                        exception=e,
                        context=None)
            raise e

    def create_redshift_table(self, schema_name: str, table_name: str, column_types: str):
        """
        This method creates a new Redhsift table

        :param schema_name: The name of the schema where the table will be located
        :param table_name: The name of the new table
        :param column_types: A partial SQL string that defines the columns and data types
        """
        table_name = update_table_name_that_starts_with_digit(table_name)
        log_message(log_level='Debug',
                    message=f'Creating redshift table "{schema_name}.{table_name}"',
                    exception=None,
                    context=None)
        try:

            create_query = f"CREATE TABLE {schema_name}.{table_name} ({column_types}"
            if table_name == 'metadata':
                create_query += f", PRIMARY KEY (extract, column_name))"
            else:
                create_query += ")"
            self.redshift_conn.run_query(create_query, False)
        except Exception as e:
            log_message(log_level='Error',
                        message=f'Error creating table {self.dbname}.{schema_name}.{table_name}',
                        exception=e,
                        context=None)
            raise e

    def add_foreign_key_constraint(self, schema_name: str, table_name: str, columns_and_references: dict[str, str]):
        """
        This method alters an existing table by adding a foreign key constraint

        :param schema_name: Name of the schema the table is located
        :param table_name: The name of the table that is being altered
        :param columns_and_references: A dictionary that maps the name of the column and the referenced table
        """
        log_message(log_level='Debug',
                    message=f'Table name before update {table_name}',
                    exception=None,
                    context=None)
        table_name = update_table_name_that_starts_with_digit(table_name)
        log_message(log_level='Debug',
                    message=f'Table name after update {table_name}',
                    exception=None,
                    context=None)
        alter_query = ''
        try:
            for column, reference in columns_and_references.items():
                updated_reference = update_table_name_that_starts_with_digit(reference.split(".")[1].lower())
                update_column = update_table_name_that_starts_with_digit(column)
                alter_query += f"""
                    ALTER TABLE {schema_name}.{table_name}
                    ADD CONSTRAINT fk_constraint_{table_name}_{column}
                    FOREIGN KEY ({update_column}) REFERENCES {schema_name}.{updated_reference}(id);
                """
            self.redshift_conn.run_query(alter_query, False)
        except Exception as e:
            raise e

    def redshift_drop_columns_from_table(self, schema_table_name: str, columns_to_remove: set[str]):
        """

        This method executes an ALTER statement on a table to drop a specified list of columns.
        :param schema_table_name: A concatenated string of the schema name and the table name with a "." delimiter
        :param columns_to_remove: A set of columns to remove from the specified table
        """
        query = ''

        schema_table = schema_table_name.split('.')

        table_name = update_table_name_that_starts_with_digit(schema_table[1])

        schema_table_name = ".".join([schema_table[0], table_name])

        if columns_to_remove:
            for column in columns_to_remove:
                column = update_table_name_that_starts_with_digit(column)
                query += f"ALTER TABLE {schema_table_name} DROP COLUMN {column}; "

        self.redshift_conn.run_query(query, False)

    def redshift_update_table(self, schema_name: str, table_name: str, new_column_names: dict[Any, tuple[Any, Any]],
                              columns_to_drop: set[str]) -> bool:
        """
        This method retrieves the current columns of a table and compares those to either a list of newly added columns or columns
        that should be dropped and updates the table appropriately
        :param schema_name: The name of the schema where the table is located
        :param table_name: The name of the table to be updated
        :param new_column_names: A dictionary that maps the new columns to the data types and length of string
        :param columns_to_drop: A set of columns that should be dropped from an existing table
        """

        table_name = update_table_name_that_starts_with_digit(table_name)
        query = f"""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE
            table_catalog = '{self.dbname}' 
            AND table_schema = '{schema_name}'
            AND table_name = '{table_name}'
        """
        redshift_table_name = f"{schema_name}.{table_name}"
        try:
            if len(columns_to_drop) > 0:
                self.redshift_drop_columns_from_table(redshift_table_name, columns_to_drop)

            current_column_dict = self.redshift_conn.get_db_column_names(query, False)
            current_columns = set(current_column_dict.keys())
            new_columns = set(new_column_names.keys())
            added_columns = new_columns - current_columns
            if current_columns == new_columns or current_columns.intersection(new_columns):
                column_lengths_have_changed = False
                for new_column, (new_data_type, new_column_length) in new_column_names.items():
                    # Get the current column length
                    current_column_length = current_column_dict[new_column][1]

                    # Helper function to convert a value to an integer if it's valid
                    def to_int(value):
                        if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
                            return None
                        try:
                            return int(value)
                        except (ValueError, TypeError):
                            return None

                    # Convert both current and new column lengths to integers, ignore if invalid
                    current_column_length_int = to_int(current_column_length)
                    new_column_length_int = to_int(new_column_length)

                    # Skip iteration if either column length is invalid
                    if current_column_length_int is None or new_column_length_int is None:
                        continue

                    # Apply the scaling factor
                    new_column_length_int *= 2

                    # Compare the lengths and update if needed
                    if current_column_length_int < new_column_length_int:
                        current_column_dict[new_column] = (current_column_dict[new_column][0], str(new_column_length_int))
                        column_lengths_have_changed = True

                if column_lengths_have_changed:
                    self.update_column_lengths(redshift_table_name, ', '.join(current_column_dict.keys()),
                                               create_sql_str(current_column_dict, False))
                else:
                    log_message(log_level='Info',
                                message=f'No columns to update for table {redshift_table_name}',
                                context=None)

            else:
                if len(added_columns) > 0:
                    self.redshift_add_columns_to_table(redshift_table_name,
                                                       {column: new_column_names[column] for column in added_columns})

            return True
        except Exception as e:
            log_message(log_level='Error',
                        message=f'Error updating table {redshift_table_name}',
                        exception=e,
                        context=None)
            raise e

    def redshift_add_columns_to_table(self, redshift_table_name, columns: dict[Any, tuple[Any, Any]]):
        """
        This method adds columns to a specified table
        :param redshift_table_name: The name of the table that is having columns added
        :param columns: A list of new columns to add
        """
        query = f"ALTER TABLE {self.dbname}.{redshift_table_name}"
        for column, (data_type, length) in columns.items():
            column = update_table_name_that_starts_with_digit(column)
            column_name = ''
            data_type = data_type.lower()

            if length is None or length == "" or (isinstance(length, float) and math.isnan(length)):
                length = 64000
            else:
                length = int(length)
                if length > 32000:
                    length = 64000
                else:
                    length *= 2

            if data_type == "id" or (column.lower() == 'id' and data_type == 'string'):
                column_name += f'"{column}" VARCHAR({length}) PRIMARY KEY, '
            elif data_type == "datetime":
                column_name += f'"{column}" TIMESTAMPTZ, '
            elif data_type == "boolean":
                column_name += f'"{column}" BOOLEAN, '
            elif data_type == "number":
                column_name += f'"{column}" NUMERIC, '
            elif data_type == "date":
                column_name += f'"{column}" DATE, '
            else:
                column_name += f'"{column}" VARCHAR({length}), '
            query += f"""
                ADD COLUMN {column_name}, 
                """
        # Remove the trailing comma and whitespace from the last line
        query = query.rstrip(", \n") + ";"
        self.redshift_conn.run_query(query, False)

    def update_column_lengths(self, schema_table_name: str, column_names: str, column_partial_query: str):

        temp_table_name = f"{self.dbname}.{schema_table_name}_temp"

        create_table_query = f"""
        CREATE TABLE {temp_table_name} ({column_partial_query}) 
        """

        insert_data_into_temp_table_query = f"""
            INSERT INTO {temp_table_name}({column_names})
            SELECT {column_names}
            FROM {self.dbname}.{schema_table_name}
        """

        drop_existing_table_query = f"""DROP TABLE {schema_table_name}"""

        change_temp_table_name_query = f"""ALTER TABLE {temp_table_name} RENAME TO {schema_table_name.split(".")[1]}"""

        self.redshift_conn.run_query(create_table_query, True)
        self.redshift_conn.run_query(insert_data_into_temp_table_query, True)
        self.redshift_conn.run_query(drop_existing_table_query, True)
        self.redshift_conn.run_query(change_temp_table_name_query, False)

    def load_full_data(self, schema_name: str, table_name: str, s3_uri: str, headers: str, extract_type: str):
        """
        This loads a specified CSV file into a specified table without duplicates.
        :param extract_type: The type of extract operation (e.g., 'full', 'incremental').
        :param schema_name: The name of the schema where the table is located.
        :param table_name: The name of the table where the data is to be loaded.
        :param s3_uri: The URI of the CSV in the S3.
        :param headers: A string of ordered headers from the CSV.
        """

        table_name = update_table_name_that_starts_with_digit(table_name)
        if s3_uri is not None:
            log_message(log_level='Info',
                        message=f'Table to be loaded: {schema_name}.{table_name}',
                        context=None)

            if extract_type == 'full':
                query = (
                    f"COPY {self.dbname}.{schema_name}.{table_name} ({headers}) FROM '{s3_uri}' "
                    f"IAM_ROLE '{self.iam_role}' "
                    f"FORMAT AS CSV "
                    f"QUOTE '\"' "
                    f"IGNOREHEADER 1 "
                    f"TIMEFORMAT 'auto' "
                    f"ACCEPTINVCHARS "
                    f"FILLRECORD "
                    f"TRUNCATECOLUMNS;"
                )

                try:
                    self.redshift_conn.run_query(query, False)
                    return True
                except Exception as e:
                    log_message(log_level='Error',
                                message=f'Error loading {schema_name}.{table_name}',
                                exception=e,
                                context=None)
                    raise e

            else:
                # Retrieve primary key columns
                primary_keys = []
                if table_name == 'metadata':
                    primary_keys = ["extract", "column_name"]
                elif table_name == 'picklist__sys':
                    primary_keys = ["object", "object_field", "picklist_value_name"]
                else:
                    primary_keys = ["id"]

                # Load into a temporary staging table
                staging_table_name = f"{table_name}_staging"
                pk_condition = " AND ".join(
                    [f"{schema_name}.{table_name}.{col} = {staging_table_name}.{col}" for col in primary_keys])

            copy_query = f"""
                    CREATE TEMP TABLE {staging_table_name} (LIKE {schema_name}.{table_name});
                    COPY {staging_table_name} ({headers}) FROM '{s3_uri}'
                    IAM_ROLE '{self.iam_role}'
                    FORMAT AS CSV
                    QUOTE '\"'
                    IGNOREHEADER 1
                    TIMEFORMAT 'auto'
                    ACCEPTINVCHARS
                    FILLRECORD
                    TRUNCATECOLUMNS;
                """

            delete_duplicates_query = f"""
                    DELETE FROM {schema_name}.{table_name}
                    USING {staging_table_name}
                    WHERE {pk_condition};
                """

            insert_query = f"""
                    INSERT INTO {schema_name}.{table_name}
                    SELECT DISTINCT * FROM {staging_table_name};
                """

            try:
                # Run the queries in sequence
                self.redshift_conn.run_query(copy_query, True)
                self.redshift_conn.run_query(delete_duplicates_query, True)
                self.redshift_conn.run_query(insert_query, False)
                return True
            except Exception as e:
                log_message(log_level='Error',
                            message=f'Error loading {schema_name}.{table_name}',
                            exception=e,
                            context=None)
                raise e

        else:
            log_message(log_level='Info',
                        message=f'Load operation for {schema_name}.{table_name} is skipped',
                        exception=None, context=None)
            return True

    def delete_data(self, schema_name: str, table_name: str, s3_file_uri: str):
        """
        This method deletes data from a specified table

        :param schema_name: The name of the schema where the table is located
        :param table_name: The name of the table that is having data deleted from
        :param s3_file_uri: The URI of the deletes file in S3
        """

        table_name = update_table_name_that_starts_with_digit(table_name)
        try:
            # create a temporary table to hold the data from the delete file
            columns = ''
            column_names = ''
            if table_name == 'picklist__sys':
                column_names += 'object || object_field || picklist_value_name'
                columns += 'object VARCHAR(255), object_field VARCHAR(255), picklist_value_name VARCHAR(255)'
            elif table_name == 'metadata':
                column_names += 'extract || column_name'
                columns += 'extract VARCHAR(255), column_name VARCHAR(255)'
            else:
                column_names += 'id'
                columns += 'id VARCHAR(255)'
            create_query = f"CREATE TEMPORARY TABLE temp_{table_name}_deletes ({columns}, deleted_date TIMESTAMPTZ)"
            self.redshift_conn.run_query(create_query, True)

            # load the data from the _deletes.csv file into the temporary table
            copy_query = f"""
                COPY temp_{table_name}_deletes FROM '{s3_file_uri}'
                IAM_ROLE '{self.iam_role}'
                FORMAT AS CSV 
                QUOTE '\"'
                IGNOREHEADER 1
                TIMEFORMAT 'auto'
                """
            self.redshift_conn.run_query(copy_query, True)
            # delete the matching rows from the target table
            delete_query = f"DELETE FROM {self.dbname}.{schema_name}.{table_name} WHERE {column_names} IN (SELECT {column_names} FROM temp_{table_name}_deletes);"
            self.redshift_conn.run_query(delete_query, False)
        except Exception as e:
            log_message(log_level='Error',
                        message=f'Something went wrong when attempting to delete the data.',
                        exception=e,
                        context=None)
            raise e

    def drop_table(self, schema_name, table_name):

        table_name = update_table_name_that_starts_with_digit(table_name)

        drop_table_query = f"DROP TABLE {self.dbname}.{schema_name}.{table_name};"

        self.redshift_conn.run_query(drop_table_query, False)
