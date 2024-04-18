#Stage1
FROM public.ecr.aws/lambda/python:3.10

# Set the working directory
WORKDIR /app

# Copy common code
COPY common /app/common

# Copy function code
COPY /app/* ${LAMBDA_TASK_ROOT}

# Install the specified packages
RUN pip install -r /app/common/requirements.txt

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD [ "run.lambda_handler" ]


