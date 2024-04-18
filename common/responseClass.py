import json


class ResponseClass:
    """
    If the lambda handler is invoked via API Gateway, this response class is returned.
    """
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def to_dict(self):
        # Check if the body is already a string (JSON serializable)
        if isinstance(self.body, str):
            body_str = self.body
        else:
            # Convert the body to a JSON serializable format
            body_str = json.dumps(self.body)

        return {
            'statusCode': self.status_code,
            'body': body_str,
            'headers': {
                'Content-Type': 'application/json'
            }
        }

    def set_body(self, updated_body):
        self.body = updated_body

    def set_status_code(self, updated_status_code):
        self.status_code = updated_status_code

    def append_body(self, additional_body):
        self.body += f'\n {additional_body}'
