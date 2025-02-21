# Direct Data API Connector for Amazon Redshift



## Introduction
[Direct Data API](https://developer.veevavault.com/directdata) allows Veeva customers to replicate their data in external data warehouses and/or data lakes. 

## Overview

![Direct Data Connector Diagram](https://github.com/veeva/Direct-Data-API-connector-for-Amazon-Redshift/blob/2e30807073334e9da71cf4331263935c09ad86af/Direct%20Data%20API%20Connector.png)

This project is a custom connector between Vault and Amazon Redshift. This connector performs the following:
1. List and download Direct Data files from Vault using Direct Data API
2. Place Direct Data files to an S3 bucket
3. Load Direct Data into an Amazon Redshift database


## Setup

The steps below outline how to create and configure resources in an AWS account to use the Direct Data API Connector.

Note: All resources should be created in the same AWS Region.

### Prerequisites

* Install [Docker](https://docs.docker.com/get-docker/)
* Install [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
* Install [Postman](https://www.postman.com/downloads/)

### ECR Repo

* Navigate to the ECR service in the AWS Console
* Under `Private registry`, select `Repositories`
* Select `Create repository`
* Configure the repository with the following settings:
  * _General Settings_: `Private`
  * _Repository name_: `cf-direct-data`
* All other settings are default. Click through to create the repository

* [Configure and Authenticate](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-configure.html) the AWS CLI for initial setup
* Using AWS CLI, authenticate Docker to the ECR Repo:
  ```
  aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com```
* Using Docker, pull the following two images from the Veeva ECR Public Repo:
  ```
  docker pull public.ecr.aws/u0l6k3p6/direct-data:lambda-latest
  docker pull public.ecr.aws/u0l6k3p6/direct-data:batch-latest
  ```
  
* Tag the images with the `cf-direct-data` ECR Repo URI that was created above:
  ```
  docker tag public.ecr.aws/u0l6k3p6/direct-data:lambda-latest {PRIVATE_REPO_URI}:lambda-latest
  docker tag public.ecr.aws/u0l6k3p6/direct-data:batch-latest {PRIVATE_REPO_URI}:batch-latest
  ```
* Push the images to the ECR Repo:
  ```
  docker push {PRIVATE_REPO_URI}:lambda-latest
  docker push {PRIVATE_REPO_URI}:batch-latest
  ```
  
### IAM
* Navigate to the IAM service in the AWS Console
* Select `Roles` and `Create Role`
* Configure the role with the following settings:
  * Step 1: 
    * _Trusted entity type_: `AWS account`
    * _An AWS account_: `This account`
    * _Use case_: `Cloudformation`
  * Step 2: Attach the following AWS managed policies. These are required to access Cloudformation and create the direct data resources.
    * AmazonAPIGatewayAdministrator
    * AmazonEC2ContainerRegistryFullAccess
    * AmazonRedshiftFullAccess
    * AmazonS3FullAccess
    * AmazonVPCFullAccess
    * AWSBatchFullAccess
    * AWSCloudFormationFullAccess
    * AWSLambda_FullAccess
    * IAMFullAccess
    * SecretsManagerReadWrite
  * Step 3:
    * Give the role an appropriate name
* All other settings are default. Click through to create the role

### Cloudformation Stack
* Download the <a href="https://github.com/veeva/Direct-Data-API-connector-for-Amazon-Redshift/blob/main/CloudFormationDirectDataTemplateLatest.yaml" download>Direct Data Cloudformation Template</a>
* Navigate to the Cloudformation service in the AWS Console
* Select `Create Stack` and `With new resources (standard)`
* Configure the Stack with the following settings:
  * Step 1: 
    * _Prepare Template_: `Choose an Existing Template`
    * _Template Source_: `Upload a template file`. 
    * Click `Choose file` and select the _Direct Data Cloudformation Template_  
  * Step 2: 
    * Give the stack an appropriate name
  * Step 3:
    * _Permissions_: Select the IAM Role created in the previous step
* All other settings are default. Click through to create the stack. When the job completes, Select the `Resources` tab to view the resources created

### Lambda Function
* Navigate to the Lambda service in the AWS Console
* Search for and select the Lambda function named `cf-direct-data`
* Select `Add trigger` from the function screen
* Configure the trigger with the following settings:
  * _Trigger configuration_: `API Gateway`
  * _Intent_: `Create a new API`
  * _API type_: `HTTP API`
  * _Security_: `Open`
* All other settings are default. Click through to create the trigger
* Copy the `API endpoint` value of the trigger and note it down separately. This will be used to invoke the integration

### S3 Bucket
* Navigate to the S3 service in the AWS Console
* Search for and select the S3 bucket named `{ACCOUNT_ID}-{REGION}-cf-direct-data`
* Copy the s3 bucket name and note it down separately. This will be used in the Direct Data configuration file
* Create a folder at the root of the bucket named `direct-data`

### Redshift Cluster
* Navigate to the Redshift service in the AWS Console
* Search for and select the Redshift cluster named `cf-direct-data`
* From the cluster screen, copy the `Endpoint` value and note it down separately. This will be used in the Direct Data configuration file
* Note: The following step is not required, but recommended for security
* From the `Actions` dropdown, select `Change admin user password`

### IAM
* Navigate to the IAM service in the AWS Console
* Select `Roles`
* Search for and select the role named `cf-direct-data-redshift-role-{REGION}`
* Copy the `ARN` value and note it down separately. This will be used in the Direct Data configuration file

### Secrets Manager
* Navigate to the Secrets Manager service in the AWS Console
* Search for and select the secret named `direct-data-config.ini`
* Select `Retrieve secret value` then `Edit`. Update the following values under the [demo] section:
  * vault_username
  * vault_password
  * vault_dns
  * redshift_host (Use the previously copied redshift endpoint. Do not include the port number/database name)
  * redshift_iam_redshift_s3_read (Use the previously copied ARN for `cf-direct-data-redshift-role-{REGION}`)
  * redshift_password (If updated in the previous step)
  * s3_bucket_name
* Additional sections can be added with different vault and/or AWS services specified for multiple Vault and database functionality. 

### VPC
* Navigate to the VPC service in the AWS Console
* Select `Route tables` from the left
* Select the table ID associated with the `cf-direct-data` VPC
* Select `Edit Routes`
* Select `Add Route`
* Add the following route:
  * Destination: `0.0.0.0/0`
  * Target: `Internet Gateway` (Select the gateway associated with the VPC)
* Save the route table

### Initial Full Extract Invocation
* Download the <a href="https://github.com/veeva/Direct-Data-API-connector-for-Amazon-RedshiftVault-Direct-Data-API-Connector/blob/main/Public%20Direct%20Data%20Lambda%20API.postman_collection.json" download>Direct Data Connector Postman Collection</a>
* Import the collection into Postman
* Open the _List and Download Direct Data Files to S3_ endpoint
* Update the URL to the previously noted `API endpoint` from the lambda trigger
* Update the body parameters with the following JSON payload:
```json
{
  "step": "retrieve", 
  "start_time": "2000-01-01T00:00Z", 
  "stop_time": "2024-04-19T00:00Z", //Update this value to the current date
  "extract_type": "full", 
  "continue_processing": true,
  "secret": "demo"
  }
```
* Click `Send`

* When manually invoking the `full` or `log` extract type process, the **List and Download Direct Data Files to S3** call will respond with the AWS Batch job name `Starting AWS Batch Job with ID: cf-direct-data-retrieve` and **Unzip Files in S3** calls will call will respond with the AWS Batch job name `Starting AWS Batch Job with ID: cf-direct-data-unzip`.


### Verify
Once both AWS Batch jobs have completed, confirm the following: 
* That the zipped file and the unzipped contents are present in the previously created S3 bucket
* All the tables were created in the specified Redshift schema and the data was loaded. This can be confirmed using [Redshift Query Editor v2](https://docs.aws.amazon.com/redshift/latest/mgmt/query-editor-v2-using.html).

### Amazon EventBridge
These schedules should be created after the initial `full` extract is invoked.
#### Incremental Schedule
* Navigate to the Amazon EventBridge service in the AWS Console
* Select `Schedules` from the left under the `Scheduler` section
* Select `Create schedule`
* Configure the schedule with the following settings:
  * _Schedule name_: `direct-data-incremental-schedule`
  * _Occurrence_: `Recurring schedule`
  * _Schedule type_: `Rate-based schedule`
  * _Rate expression_: `15 minutes`
  * _Flexible time window_: `Off`
  * _Start date and time_: Insert the target start time for when this schedule should run
* Select `Next`
* Configure the next page with the following settings:
  * _Template targets_: `AWS Lambda Invoke`
  * _Lambda function_: `cf-direct-data`
  * _Payload_: 
```json
{ 
  "step": "retrieve", 
  "extract_type": "incremental", 
  "continue_processing": true,
  "secret": "demo"
}
```
* Select `Next`
* On the next page select `Next`
* Select `Create schedule`

#### Log Schedule
* Follow the same steps as the **Incremental Schedule** except changing the following fields:
  * _Rate expression_: `24 hours`
  * _Start date and time_: Tomorrow's date at 12 AM
  * _Payload_: 
```json
{ 
  "step": "retrieve", 
  "extract_type": "log", 
  "continue_processing": true,
  "secret": "demo"
}
```

### Troubleshooting
If errors are encountered, the logs for the Lambda function can be located on CloudWatch whereas the AWS Batch job logging can be located within the previous job that ran. 

## Support
Questions, enhancement requests, or issues should be posted in the [Vault for Developers](https://veevaconnect.com/communities/ATeJ3k8lgAA/posts) community on Veeva Connect. 
Partners should discuss these topics with their Veeva counterparts. 