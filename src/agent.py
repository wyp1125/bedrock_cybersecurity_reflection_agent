import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel

try:
    from bedrock_agentcore import BedrockAgentCoreApp
except ImportError:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp


load_dotenv()

app = BedrockAgentCoreApp()

BASE_DIR = Path(__file__).parent

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
MAX_ROUNDS = int(os.getenv("MAX_REFLECTION_ROUNDS", "5"))
TARGET_SCORE = int(os.getenv("TARGET_REFLECTION_SCORE", "4"))


def read_prompt_file(filename: str) -> str:
    path = BASE_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing required prompt file: {path}")

    return path.read_text(encoding="utf-8").strip()


gen_prompt = read_prompt_file("generator_prompt.txt")
eval_prompt = read_prompt_file("evaluator_prompt.txt")


def message_to_text(response: Any) -> str:
    """
    Extracts text from common Strands / Bedrock response shapes.
    """
    message = getattr(response, "message", response)

    if isinstance(message, str):
        return message.strip()

    if isinstance(message, dict):
        content = message.get("content")

        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        parts.append(str(block["text"]))
                    elif block.get("type") == "text" and "content" in block:
                        parts.append(str(block["content"]))
                elif isinstance(block, str):
                    parts.append(block)

            if parts:
                return "".join(parts).strip()

        if "text" in message:
            return str(message["text"]).strip()

        return json.dumps(message, ensure_ascii=False)

    return str(message).strip()


def extract_json_text(text: str) -> str:
    """
    Extracts JSON from plain text or fenced markdown.
    """
    text = str(text).strip()

    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    start = min(
        [i for i in [text.find("{"), text.find("[")] if i != -1],
        default=-1
    )

    if start == -1:
        return text

    return text[start:].strip()


def safe_json_loads(text: str) -> Optional[Any]:
    try:
        return json.loads(extract_json_text(text))
    except Exception:
        return None


def normalize_payload(payload: Any) -> Dict[str, Any]:
    """
    Handles direct AgentCore payloads and Lambda/API Gateway-style body payloads.
    """
    if payload is None:
        return {}

    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"prompt": payload}

    if isinstance(payload, dict):
        body = payload.get("body")

        if isinstance(body, str):
            try:
                parsed_body = json.loads(body)
                if isinstance(parsed_body, dict):
                    return parsed_body
            except json.JSONDecodeError:
                return {"prompt": body}

        return payload

    return {"prompt": str(payload)}


model_provider = BedrockModel(
    model_id=MODEL_ID,
    region_name=AWS_REGION,
)

generator_agent = Agent(
    model=model_provider,
    system_prompt=gen_prompt,
)

evaluator_agent = Agent(
    model=model_provider,
    system_prompt=eval_prompt,
)


def run_reflection_loop(cyber_issue: str) -> Dict[str, Any]:
    """
    Executes an iterative generator/evaluator reflection loop.
    """
    feedback_history = ""
    latest_mappings = ""
    score = 1
    critique = "No critique provided."

    print(f"[START] Analyzing issue: {cyber_issue[:120]}")

    for current_round in range(1, MAX_ROUNDS + 1):
        print(f"[ROUND {current_round}] Starting generator pass")

        gen_input = f"Issue:\n{cyber_issue}"

        if feedback_history:
            gen_input += (
                f"\n\nPrevious Iteration Mappings:\n{latest_mappings}"
                f"\n\nAuditor Critique to Resolve:\n{feedback_history}"
            )

        gen_response = generator_agent(gen_input)
        latest_mappings = message_to_text(gen_response)

        print(f"[ROUND {current_round}] Generator completed")

        eval_input = (
            f"Original Issue:\n{cyber_issue}"
            f"\n\nProposed Mappings:\n{latest_mappings}"
        )

        eval_response = evaluator_agent(eval_input)
        eval_output_raw = message_to_text(eval_response)

        print(f"[ROUND {current_round}] Evaluator completed")

        eval_data = safe_json_loads(eval_output_raw)

        if isinstance(eval_data, dict):
            try:
                score = int(eval_data.get("score", 1))
            except Exception:
                score = 1

            critique = str(eval_data.get("critique", "No critique provided."))
        else:
            print(f"[ROUND {current_round}] Evaluator returned invalid JSON")
            score = 1
            critique = (
                "Your response format was invalid JSON. "
                "Return only valid JSON with keys: score and critique."
            )

        if score >= TARGET_SCORE:
            final_mappings_json = safe_json_loads(latest_mappings)

            return {
                "status": "satisfied",
                "rounds_completed": current_round,
                "auditor_score": score,
                "mappings": final_mappings_json if final_mappings_json is not None else latest_mappings,
                "auditor_critique": critique,
            }

        feedback_history = critique

    final_mappings_json = safe_json_loads(latest_mappings)

    return {
        "status": "max_rounds_reached",
        "rounds_completed": MAX_ROUNDS,
        "auditor_score": score,
        "mappings": final_mappings_json if final_mappings_json is not None else latest_mappings,
        "auditor_critique": feedback_history,
    }


@app.entrypoint
def handle_agent_invocation(payload: Any) -> Dict[str, Any]:
    """
    Bedrock AgentCore Runtime entrypoint.

    Expected direct payload:
    {
      "prompt": "Vulnerability context description"
    }
    """
    normalized = normalize_payload(payload)
    user_prompt = normalized.get("prompt") or normalized.get("input") or normalized.get("message")

    if not user_prompt:
        return {
            "error": "Missing required prompt. Expected key: 'prompt'."
        }

    try:
        result = run_reflection_loop(str(user_prompt))
        return {"result": result}
    except Exception as exc:
        print(f"[ERROR] Agent invocation failed: {exc}")
        return {
            "error": "Agent invocation failed.",
            "details": str(exc),
        }


if __name__ == "__main__":
    app.run()