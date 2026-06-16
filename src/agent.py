import json
from pathlib import Path

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

with open(BASE_DIR / "generator_prompt.txt", "r", encoding="utf-8") as f:
    gen_prompt = f.read()

with open(BASE_DIR / "evaluator_prompt.txt", "r", encoding="utf-8") as f:
    eval_prompt = f.read()


def message_to_text(response) -> str:
    """
    Safely extracts text from a Strands response object.
    """
    message = getattr(response, "message", response)

    if isinstance(message, str):
        return message

    if isinstance(message, dict):
        content = message.get("content", [])
        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
            ).strip()
        return json.dumps(message)

    return str(message)


def extract_json_text(text: str) -> str:
    """
    Removes common markdown JSON fences before json.loads().
    """
    text = str(text).strip()

    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    return text.strip()


model_provider = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6",
    region_name="us-east-1"
)

generator_agent = Agent(
    model=model_provider,
    system_prompt=gen_prompt
)

evaluator_agent = Agent(
    model=model_provider,
    system_prompt=eval_prompt
)


def run_reflection_loop(cyber_issue: str) -> dict:
    """
    Executes an iterative reflection cycle up to 5 rounds.
    Terminates early if the evaluator score is >= 4.
    """
    current_round = 1
    max_rounds = 5
    target_score = 4

    feedback_history = ""
    latest_mappings = ""
    score = 1

    print(f"\n[STARTING ITERATIVE ARCHITECTURE] Analyzing issue: {cyber_issue[:70]}...")

    while current_round <= max_rounds:
        print(f"\n--- Reflection Loop - Round {current_round} ---")

        gen_input = f"Issue: {cyber_issue}"

        if feedback_history:
            gen_input += (
                f"\n\nPrevious Iteration Mappings:\n{latest_mappings}"
                f"\n\nAuditor Critique to Resolve:\n{feedback_history}"
            )

        gen_response = generator_agent(gen_input)
        latest_mappings = message_to_text(gen_response)

        print(f"[Generator Output]:\n{latest_mappings}")

        eval_input = (
            f"Original Issue:\n{cyber_issue}"
            f"\n\nProposed Mappings:\n{latest_mappings}"
        )

        eval_response = evaluator_agent(eval_input)
        eval_output_raw = message_to_text(eval_response)

        print(f"[Evaluator Output]:\n{eval_output_raw}")

        try:
            clean_json = extract_json_text(eval_output_raw)
            eval_data = json.loads(clean_json)

            score = int(eval_data.get("score", 1))
            critique = eval_data.get("critique", "No critique provided.")
        except Exception as e:
            print(f"[JSON Extraction Warning] Failed to parse evaluator output: {e}")
            score = 1
            critique = (
                "Your response format was invalid JSON. "
                "Re-generate adhering precisely to the specified template schema."
            )

        if score >= target_score:
            print(
                f"\n[SUCCESS] Compliance criteria met with auditor score "
                f"{score}/5 in Round {current_round}."
            )

            try:
                final_mappings_json = json.loads(extract_json_text(latest_mappings))
            except Exception:
                final_mappings_json = latest_mappings

            return {
                "status": "satisfied",
                "rounds_completed": current_round,
                "auditor_score": score,
                "mappings": final_mappings_json,
                "auditor_critique": critique
            }

        feedback_history = critique
        current_round += 1

    print(
        f"\n[TIMEOUT] Reached max loop iterations "
        f"({max_rounds} rounds) without achieving target baseline."
    )

    try:
        final_mappings_json = json.loads(extract_json_text(latest_mappings))
    except Exception:
        final_mappings_json = latest_mappings

    return {
        "status": "max_rounds_reached",
        "rounds_completed": max_rounds,
        "auditor_score": score,
        "mappings": final_mappings_json,
        "auditor_critique": feedback_history
    }


@app.entrypoint
def handle_agent_invocation(payload: dict) -> dict:
    """
    Bedrock AgentCore Runtime entrypoint.

    Expected payload:
    {
      "prompt": "Vulnerability context description"
    }
    """
    user_prompt = payload.get("prompt")

    if not user_prompt:
        return {
            "error": "Missing mandatory 'prompt' key in invocation payload."
        }

    loop_result = run_reflection_loop(user_prompt)

    return {
        "result": loop_result
    }


if __name__ == "__main__":
    app.run()