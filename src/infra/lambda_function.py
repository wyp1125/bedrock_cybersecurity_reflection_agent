import base64
import json
import os
import uuid
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError


AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN")
AGENT_RUNTIME_QUALIFIER = os.environ.get("AGENT_RUNTIME_QUALIFIER")

# AWS_REGION is automatically provided by Lambda.
REGION = os.environ.get("AWS_REGION") or "us-east-1"

client = boto3.client("bedrock-agentcore", region_name=REGION)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
    )

    if method == "OPTIONS":
        return build_response(204, {})

    if not AGENT_RUNTIME_ARN:
        return build_response(500, {
            "error": "Configuration error: AGENT_RUNTIME_ARN is not set."
        })

    try:
        body_data = parse_body(event)
    except ValueError as exc:
        return build_response(400, {
            "error": "Malformed request body.",
            "details": str(exc)
        })

    user_prompt = (
        body_data.get("issue")
        or body_data.get("prompt")
        or body_data.get("input")
        or body_data.get("message")
    )

    if not user_prompt:
        return build_response(400, {
            "error": "Missing required input. Provide one of: issue, prompt, input, or message."
        })

    try:
        response = invoke_agentcore(event, str(user_prompt))
        return build_response(200, response)

    except ClientError as exc:
        error = exc.response.get("Error", {})
        return build_response(502, {
            "error": "AgentCore invocation failed.",
            "code": error.get("Code"),
            "details": error.get("Message")
        })

    except Exception as exc:
        return build_response(500, {
            "error": "Internal gateway routing exception during execution.",
            "details": str(exc)
        })


def invoke_agentcore(event: Dict[str, Any], prompt: str) -> Any:
    invoke_args = {
        "agentRuntimeArn": AGENT_RUNTIME_ARN,
        "accept": "application/json",
        "contentType": "application/json",
        "payload": json.dumps({"prompt": prompt}).encode("utf-8"),
        "runtimeSessionId": get_session_id(event),
    }

    if AGENT_RUNTIME_QUALIFIER:
        invoke_args["qualifier"] = AGENT_RUNTIME_QUALIFIER

    response = client.invoke_agent_runtime(**invoke_args)

    response_body = read_agentcore_payload(response.get("payload"))
    parsed = parse_agent_response(response_body)

    if isinstance(parsed, dict) and "result" in parsed:
        return parsed["result"]

    return parsed


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")

    if body is None:
        return event if isinstance(event, dict) else {}

    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, dict):
        return body

    if isinstance(body, str):
        if not body.strip():
            return {}

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc

        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object.")

        return parsed

    raise ValueError("Unsupported request body format.")


def get_session_id(event: Dict[str, Any]) -> str:
    headers = normalize_headers(event.get("headers"))

    session_id = (
        headers.get("x-session-id")
        or headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
        or event.get("requestContext", {}).get("requestId")
        or str(uuid.uuid4())
    )

    return str(session_id)[:128]


def normalize_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not headers:
        return {}

    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if value is not None
    }


def read_agentcore_payload(payload_stream: Any) -> str:
    if payload_stream is None:
        return ""

    if hasattr(payload_stream, "read"):
        raw = payload_stream.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    chunks = []

    for stream_event in payload_stream:
        if "chunk" in stream_event:
            chunk = stream_event["chunk"]
            chunk_bytes = chunk.get("bytes") or chunk.get("data")
            if chunk_bytes:
                chunks.append(decode_chunk(chunk_bytes))

        elif "payloadPart" in stream_event:
            chunk_bytes = stream_event["payloadPart"].get("bytes")
            if chunk_bytes:
                chunks.append(decode_chunk(chunk_bytes))

    return "".join(chunks)


def decode_chunk(chunk: Any) -> str:
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8")
    return str(chunk)


def parse_agent_response(response_body: str) -> Any:
    if not response_body:
        return {}

    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        return {
            "raw_response": response_body
        }


def build_response(status_code: int, payload: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
            "Access-Control-Allow-Methods": "POST,OPTIONS",
            "Access-Control-Allow-Headers": (
                "Content-Type,"
                "X-Amz-Date,"
                "Authorization,"
                "X-Api-Key,"
                "X-Session-Id,"
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
            )
        },
        "body": json.dumps(payload, ensure_ascii=False)
    }