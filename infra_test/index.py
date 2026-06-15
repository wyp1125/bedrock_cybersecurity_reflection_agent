# infra_test/index.py
import json
import boto3
import os

def handler(event, context):
    # Extract structural environment handles deployed via your Terraform pipeline
    agent_id = os.environ.get("BEDROCK_AGENT_ID", "NOT_CONFIGURED")
    agent_alias_id = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "TSTALIASID") 
    
    # Extract the user's inquiry text string safely
    prompt = event.get("prompt", "Verify system operational baseline metrics.")
    session_id = event.get("session_id", "canary-test-session-001")
    
    client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    
    try:
        # Pass the raw 10-character alphanumeric ID directly to satisfy AWS validation constraints
        response = client.invoke_agent(
            agentId=agent_id,
            agentAliasId=agent_alias_id,
            sessionId=session_id,
            inputText=prompt
        )
        
        # Stream and consolidate chunk payloads returned by the model
        completion = ""
        for event in response.get("completion", []):
            if "chunk" in event:
                completion += event["chunk"].get("bytes", b"").decode("utf-8")
                
        return {
            "statusCode": 200,
            "body": {
                "message": "Canary Agent invocation succeeded.",
                "agent_response": completion
            }
        }
        
    except Exception as e:
        return {
            "statusCode": 500,
            "body": {
                "message": "Agent communication failed.",
                "error": str(e)
            }
        }