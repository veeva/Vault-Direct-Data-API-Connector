import json


class IntegrationRequestClass:
    """
    This class ingests parameters provided by AWS EventBridge, AWS API Gateway, or AWS Batch and maps them based on the
    source service and how the parameters are structured.
    """

    def __init__(self, event):
        """
        This retrieves the event form an AWS service source and maps the parameters accordingly
        :param event: An AWS service event
        """
        self.event = event
        self.is_api_gateway = self.detect_api_gateway()
        self.http_method = None
        self.resource = None
        self.body = None
        self.step = None
        self.extract_type = None
        self.start_time = None
        self.stop_time = None
        self.starting_directory = None
        self.direct_data_listing_response = None
        self.target_directory = None
        self.source_file = None
        self.continue_processing = None

        # Map data based on event type
        if self.is_api_gateway:
            self.map_api_gateway_data()
        else:
            self.map_direct_lambda_data()

    def detect_api_gateway(self):
        """
        Determines if the parameters are coming from an AWS API gateway
        :return: A boolean that signifies whether the request is coming from an AWS API Gateway or not
        """
        # Check if the event contains API Gateway specific properties
        return 'httpMethod' in self.event and 'resource' in self.event

    def map_api_gateway_data(self):
        """
        This method maps the incoming parameters to the appropriate variables
        if the parameters are retrieved from AWS API Gateway.
        """
        # Mapping API Gateway data
        self.http_method = self.event['httpMethod']
        self.resource = self.event['resource']
        self.body = json.loads(self.event.get('body', '{}'))
        self.step = self.body.get('step')
        self.extract_type = self.body.get('extract_type')
        self.start_time = self.body.get('start_time')
        self.stop_time = self.body.get('stop_time')
        self.starting_directory = self.body.get('starting_directory')
        self.direct_data_listing_response = self.body.get('direct_data_listing_response')
        self.target_directory = self.body.get('target_directory')
        self.source_file = self.body.get('source_file')
        self.continue_processing = self.body.get('continue_processing')
        # Map other API Gateway specific data as needed

    def map_direct_lambda_data(self):
        """
        This maps the parameters that is coming from an AWS Lambda service
        """

        # data = self.event.get('data', {})
        self.step = self.event.get('step')
        self.extract_type = self.event.get('extract_type')
        self.start_time = self.event.get('start_time')
        self.stop_time = self.event.get('stop_time')
        self.starting_directory = self.event.get('starting_directory')
        self.direct_data_listing_response = self.event.get('direct_data_listing_response')
        self.target_directory = self.event.get('target_directory')
        self.source_file = self.event.get('source_file')
        self.continue_processing = self.event.get('continue_processing')


