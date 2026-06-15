import json
import os
import boto3

# The unique AWS Resource Name (ARN) assigned to your deployed AgentCore Runtime.
# This should be injected into your Lambda function's environment variables.
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN")

# Initialize the Bedrock AgentCore Data Plane client
# Note: Ensure the client targets the correct region where your agent is hosted.
client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-west-2"))

def lambda_handler(event, context):
    """
    AWS Lambda handler designed for API Gateway Lambda Proxy Integration.
    Invokes the deployed Amazon Bedrock AgentCore Runtime using the official AWS SDK.
    """
    if not AGENT_RUNTIME_ARN:
        return build_response(500, {"error": "Configuration error: AGENT_RUNTIME_ARN environment variable is not set."})

    # 1. Parse and validate the incoming payload from API Gateway
    try:
        body_str = event.get("body", "{}") or "{}"
        body_data = json.loads(body_str)
        user_cyber_issue = body_data.get("issue")
    except (json.JSONDecodeError, TypeError) as e:
        return build_response(400, {"error": "Malformed JSON payload.", "details": str(e)})

    if not user_cyber_issue:
        return build_response(400, {"error": "Missing mandatory parameter: 'issue' field is required."})

    # 2. Package payload into the {"prompt": "..."} format expected by agent.py
    agent_payload = {"prompt": user_cyber_issue}
    
    try:
        # 3. Programmatically invoke the AgentCore Runtime container cluster via AWS SDK
        # This automatically handles AWS SigV4 signing under the hood
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            accept="application/json",
            contentType="application/json",
            payload=json.dumps(agent_payload).encode("utf-8")
        )
        
        # 4. Read the streaming blob response from the agent runtime
        response_body = response["payload"].read().decode("utf-8")
        agent_raw_output = json.loads(response_body)
        
        # Extract the final result returned from your run_reflection_loop inside agent.py
        agent_result = agent_raw_output.get("result", {})
        
        return build_response(200, agent_result)

    except client.exceptions.ClientError as e:
        # Catch specific AWS IAM permission, throttle, or missing asset errors
        return build_response(400, {
            "error": "AWS Bedrock AgentCore client exception occurred.",
            "details": e.response["Error"]["Message"]
        })
        
    except Exception as e:
        # Catch generic system or timeout errors
        return build_response(500, {
            "error": "Internal gateway routing exception during execution.",
            "details": str(e)
        })

def build_response(status_code: int, payload: dict) -> dict:
    """
    Generates a structured dictionary compliant with the 
    API Gateway Lambda Proxy Integration response standard.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Adjust to your custom domain in production
            "Access-Control-Allow-Methods": "POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key"
        },
        "body": json.dumps(payload)
    }