#!/bin/bash

# Assign the first script argument to the target_directory variable
target_directory="$1"

# Check if the target_directory is empty
if [ -z "$target_directory" ]; then
    printf "Target directory is being set to the current working directory\n"
    target_directory="$(pwd)"
fi

# Navigate to the directory containing the split files
cd "$target_directory" || exit

# Prompt the user to enter Vault dns
printf "Please enter Vault dns: "
read -r dns

# Prompt the user to enter authentication type
printf "Basic (b) or Session ID (s) authentication type: "
read -r auth_type

# Convert input to lowercase
auth_type=$(printf "%s" "$auth_type" | tr '[:upper:]' '[:lower:]')

# Check the lowercase input and act accordingly
if [[ "$auth_type" == "b" || "$auth_type" == "basic" ]]; then
    printf "Selected authentication type: Basic\n"

    printf "Please enter Vault username: "
    read -r username

    printf "Please enter Vault password: "
    read -rs password
    printf "\n"

    basic_response=$(curl -s -X POST https://"$dns"/api/v24.1/auth \
                     -H "Content-Type: application/x-www-form-urlencoded" \
                     -H "Accept: application/json" \
                     -d "username=$username&password=$password")

    basic_response_status=$(jq -r '.responseStatus' <<< "$basic_response")

    if [ "$basic_response_status" != "SUCCESS" ]; then
        printf "Authentication failed. Exiting script.\n"
        exit 1
    else
        printf "Authentication succeeded.\n"
        session_id=$(jq -r '.sessionId' <<< "$basic_response")
    fi

elif [[ "$auth_type" == "s" || "$auth_type" == "session" ]]; then
    printf "Selected authentication type: Session ID\n"

    printf "Please enter Vault session Id: "
    read -rs session_id
    printf "\n"

    session_auth_response=$(curl -s -X GET "https://$dns/api/" \
                            -H "Authorization: $session_id")

    session_auth_response_status=$(jq -r '.responseStatus' <<< "$session_auth_response")

    if [ "$session_auth_response_status" == "SUCCESS" ]; then
        if jq -e '.values."v24.1"' > /dev/null <<< "$session_auth_response"; then
            printf "The session ID is valid for this Vault.\n"
        else
            printf "The session ID is not valid for this Vault.\n"
        fi
    else
        printf "Failed to retrieve API versions for this Vault with the following response status: %s. Exiting script.\n" "$session_auth_response_status"
        exit 1
    fi
else
    printf "Invalid authentication type. Please choose 'b' for Basic or 's' for Session ID.\n"
    exit 1
fi

# Prompt the user to enter extract type, start time, and stop time
printf "Please enter the extract type: "
read -r extract_type

printf "Please enter the start time: "
read -r start_time

printf "Please enter the stop time: "
read -r stop_time


# Perform the API call to retrieve the list of direct data files
direct_data_file_list_response=$(curl -s -X GET -H "Authorization: $session_id" \
                                -H "Accept: application/json" \
                                "https://$dns/api/v24.1/services/directdata/files?extract_type=$extract_type&start_time=$start_time&stop_time=$stop_time")


# Extract the response status from the API response
response_status=$(jq -r '.responseStatus' <<< "$direct_data_file_list_response")

echo "$response_status"
# Check if the API call was successful
if [ "$response_status" != "SUCCESS" ]; then
    printf "Retrieve Available Direct Data Files call failed. Exiting script.\n"
    exit 1
else
    printf "Retrieve Available Direct Data Files call succeeded.\n"
    data=$(jq -r '.data | last' <<< "$direct_data_file_list_response")
    fileparts=$(jq -r '.fileparts' <<< "$data")

    # If there are multiple file parts, handle them
    if [ "$fileparts" -gt 1 ]; then
        filepart_details=$(jq -c '.filepart_details[]' <<< "$data")
        while IFS= read -r filepart_detail; do
            filepart_url=$(jq -r '.url' <<< "$filepart_detail")
            output_filepart_name=$(jq -r '.filename' <<< "$filepart_detail")
            curl -o "$output_filepart_name" -X GET -H "Authorization: $session_id" \
                                  -H "Accept: application/json" \
                                  "$filepart_url"
        done <<< "$filepart_details"

        # Combine the file parts and extract the tar file
        filename=$(jq -r '.filename' <<< "$data")
        cat "$filename."* > "$filename"
        tar -xzvf "$filename" -C "$target_directory"
    else
        printf "Only one file part.\n"

        # Download the single file part
        filepart_detail=$(jq -c '.filepart_details[0]' <<< "$data")
        filepart_url=$(jq -r '.url' <<< "$filepart_detail")
        output_filepart_name=$(jq -r '.filename' <<< "$data")
        curl -o "$output_filepart_name" -X GET -H "Authorization: $session_id" \
            -H "Accept: application/json" "$filepart_url"

        # Extract single file using tar
        filename=$(jq -r '.filename' <<< "$data")
        name=$(jq -r '.name' <<< "$data")
        full_path="$target_directory/$name"

        if [ ! -d "$full_path" ]; then
            # Directory does not exist, create it
            mkdir -p "$full_path"
            printf "Directory '%s' created.\n" "$full_path"
        else
            printf "Directory '%s' already exists.\n" "$full_path"
        fi

        tar -xzvf "$filename" -C "$full_path"
    fi
fi
