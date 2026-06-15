# infra-test/local_test.py
import json
import boto3
import yaml
import os

def run_local_canary_test():
    config_path = os.path.join(os.path.dirname(__file__), "../config.yaml")
    
    print("📖 Reading configuration file...")
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        
    bucket_name = config["infrastructure"]["terraform_backend"]["s3_bucket"]
    state_key = config["infrastructure"]["terraform_backend"]["test_state_key"]
    aws_region = config["infrastructure"]["aws_region"]
    
    print(f"🔄 Fetching target state tracking metrics from s3://{bucket_name}/{state_key}...")
    s3 = boto3.client("s3", region_name=aws_region)
    
    try:
        response = s3.get_object(Bucket=bucket_name, Key=state_key)
        state_data = json.loads(response["Body"].read().decode("utf-8"))
        
        outputs = state_data.get("outputs", {})
        lambda_name = outputs.get("lambda_function_name", {}).get("value")
        
        if not lambda_name:
            raise ValueError("Failed to extract active deployment targets from state mapping.")
            
        print(f"✅ Found active Lambda target: '{lambda_name}'")
        
        print("\n🚀 Sending test invocation payload...")
        lambda_client = boto3.client("lambda", region_name=aws_region)
        
        test_payload = {
            "prompt": "What is the primary objective of the NIST Cybersecurity Framework?",
            "session_id": "local-workstation-canary-001"
        }
        
        invocation_response = lambda_client.invoke(
            FunctionName=lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(test_payload)
        )
        
        result = json.loads(invocation_response["Payload"].read().decode("utf-8"))
        print("\n📥 Response payload received from Lambda Execution Plane:")
        print(json.dumps(result, indent=2))
        
    except Exception as e:
        print(f"❌ Verification run aborted: {str(e)}")

if __name__ == "__main__":
    run_local_canary_test()