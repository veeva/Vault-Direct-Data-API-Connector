#Stage1
FROM public.ecr.aws/lambda/python:3.10

# Set the working directory
WORKDIR /job

# Copy common code
COPY common /job/common

# Copy the run.py file into the container
COPY /job/run.py .

# Install dependencies
RUN pip install -r /job/common/requirements.txt

# Set the entrypoint to execute the Python script
ENTRYPOINT ["python","run.py"]


