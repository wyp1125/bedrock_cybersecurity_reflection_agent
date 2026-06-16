import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from strands import Agent
from strands.models import BedrockModel

try:
    from bedrock_agentcore import BedrockAgentCoreApp
except ImportError:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp


app = BedrockAgentCoreApp()

BASE_DIR = Path(__file__).parent

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1"))

MAX_ROUNDS = 2
TARGET_SCORE = 4

_generator_agent: Optional[Agent] = None
_evaluator_agent: Optional[Agent] = None


def read_prompt_file(filename: str) -> str:
    path = BASE_DIR / filename

    if not path.exists():
        raise FileNotFoundError(f"Missing required prompt file: {path}")

    return path.read_text(encoding="utf-8").strip()


def get_generator_agent() -> Agent:
    global _generator_agent

    if _generator_agent is None:
        model = BedrockModel(
            model_id=MODEL_ID,
            region_name=BEDROCK_REGION,
        )

        _generator_agent = Agent(
            model=model,
            system_prompt=read_prompt_file("generator_prompt.txt"),
        )

    return _generator_agent


def get_evaluator_agent() -> Agent:
    global _evaluator_agent

    if _evaluator_agent is None:
        model = BedrockModel(
            model_id=MODEL_ID,
            region_name=BEDROCK_REGION,
        )

        _evaluator_agent = Agent(
            model=model,
            system_prompt=read_prompt_file("evaluator_prompt.txt"),
        )

    return _evaluator_agent


def response_to_text(response: Any) -> str:
    message = getattr(response, "message", response)

    if isinstance(message, str):
        return message.strip()

    if isinstance(message, dict):
        content = message.get("content", [])

        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
            ).strip()

        return json.dumps(message, ensure_ascii=False)

    return str(message).strip()


def extract_json_text(text: str) -> str:
    text = str(text).strip()

    fenced = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )

    if fenced:
        return fenced.group(1).strip()

    first_array = text.find("[")
    first_object = text.find("{")

    starts = [i for i in [first_array, first_object] if i != -1]

    if not starts:
        return text

    return text[min(starts):].strip()


def safe_json_loads(text: str) -> Optional[Any]:
    try:
        return json.loads(extract_json_text(text))
    except Exception:
        return None


def parse_evaluation(text: str) -> Dict[str, Any]:
    parsed = safe_json_loads(text)

    if not isinstance(parsed, dict):
        return {
            "score": 1,
            "critique": "Evaluator returned invalid JSON."
        }

    try:
        score = int(parsed.get("score", 1))
    except Exception:
        score = 1

    return {
        "score": score,
        "critique": str(parsed.get("critique", "No critique provided."))
    }


def normalize_payload(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {"prompt": payload}
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


def run_reflection_loop(issue: str) -> Dict[str, Any]:
    generator_agent = get_generator_agent()
    evaluator_agent = get_evaluator_agent()

    latest_mapping = ""
    critique = ""
    final_score = 1
    rounds_completed = 0

    for round_num in range(1, MAX_ROUNDS + 1):
        rounds_completed = round_num

        if round_num == 1:
            generator_input = f"Issue:\n{issue}"
        else:
            generator_input = f"""
Issue:
{issue}

Previous Mapping:
{latest_mapping}

Auditor Feedback:
{critique}

Revise the mappings to address the auditor feedback.
Return only valid JSON in the required mapping format.
""".strip()

        print(f"[ROUND {round_num}] Generator starting")
        generator_response = generator_agent(generator_input)
        latest_mapping = response_to_text(generator_response)
        print(f"[ROUND {round_num}] Generator completed")

        evaluator_input = f"""
Original Issue:
{issue}

Proposed Mappings:
{latest_mapping}
""".strip()

        print(f"[ROUND {round_num}] Evaluator starting")
        evaluator_response = evaluator_agent(evaluator_input)
        evaluator_text = response_to_text(evaluator_response)
        evaluation = parse_evaluation(evaluator_text)
        print(f"[ROUND {round_num}] Evaluator completed")

        final_score = evaluation["score"]
        critique = evaluation["critique"]

        print(f"[ROUND {round_num}] Score: {final_score}")

        if final_score >= TARGET_SCORE:
            break

    mappings_json = safe_json_loads(latest_mapping)

    return {
        "status": "satisfied" if final_score >= TARGET_SCORE else "max_rounds_reached",
        "rounds_completed": rounds_completed,
        "auditor_score": final_score,
        "auditor_critique": critique,
        "mappings": mappings_json if mappings_json is not None else latest_mapping,
    }


@app.entrypoint
def invoke(payload: Any) -> Dict[str, Any]:
    normalized = normalize_payload(payload)

    issue = (
        normalized.get("prompt")
        or normalized.get("issue")
        or normalized.get("input")
        or normalized.get("message")
    )

    if not issue:
        return {
            "error": "Missing required input. Provide one of: prompt, issue, input, or message."
        }

    try:
        return run_reflection_loop(str(issue))
    except Exception as exc:
        print(f"[ERROR] Agent invocation failed: {exc}")
        return {
            "error": "Agent invocation failed.",
            "details": str(exc),
        }


if __name__ == "__main__":
    app.run()
