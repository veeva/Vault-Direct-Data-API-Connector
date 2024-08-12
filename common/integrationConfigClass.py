import configparser
import boto3
from botocore.exceptions import ClientError

from .log_message import log_message


class IntegrationConfigClass:
    """
    Used to get all the required args from AWS Secrets Manager
    """
    def __init__(self, region: str, secret_name: str):
        self._config = None
        self.region = region
        self.secret_name = secret_name
    @property
    def config(self):
        """
        This returns configuration parser to parse the AWS Secrets Manager data

        :return: The parsed configuration
        """
        self._config = configparser.ConfigParser()
        config_str = self.get_secret
        self._config.read_string(config_str)
        return self._config
    @property
    def get_secret(self) -> str:
        """
        This method retrieves tha actual secrets from AWS Secrets Manager

        :return: The parsed secrets
        """
        # Create a Secrets Manager client
        session = boto3.session.Session()
        secret_name = self.secret_name
        client = session.client(
            service_name='secretsmanager',
            region_name=self.region
        )
        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=secret_name
            )
        except ClientError as e:
            # For a list of exceptions thrown, see
            # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
            log_message(log_level='Error',
                        message=f'Could not retrieve secrets',
                        exception=e, context=None)
        # Decrypts secret using the associated KMS key.
        secret = get_secret_value_response['SecretString']
        return secret
