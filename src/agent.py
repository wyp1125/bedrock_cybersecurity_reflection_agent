import os
import json
from pathlib import Path
from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Load local environment flags and secret management frameworks
load_dotenv()

# Initialize the Bedrock AgentCore service runner
app = BedrockAgentCoreApp()

# 1. Read localized actor-critic orchestration system prompts
BASE_DIR = Path(__file__).parent
with open(BASE_DIR / "generator_prompt.txt", "r") as f:
    gen_prompt = f.read()
with open(BASE_DIR / "evaluator_prompt.txt", "r") as f:
    eval_prompt = f.read()

# 2. Instantiate isolated Claude 4.6 runtimes for both workflows
model_provider = BedrockModel(model_id="anthropic.claude-sonnet-4-6-20260217-v1:0")

generator_agent = Agent(model=model_provider, system_prompt=gen_prompt)
evaluator_agent = Agent(model=model_provider, system_prompt=eval_prompt)

def run_reflection_loop(cyber_issue: str) -> dict:
    """
    Executes an iterative reflection cycle up to a maximum of 5 rounds.
    The loop terminates early if the auditor awards a score of 4 or 5 out of 5.
    """
    current_round = 1
    max_rounds = 5
    target_score = 4
    
    # Track the ongoing state transitions through loop iterations
    feedback_history = ""
    latest_mappings = ""

    print(f"\n[STARTING ITERATIVE ARCHITECTURE] Analyzing issue: {cyber_issue[:70]}...")

    while current_round <= max_rounds:
        print(f"\n--- Reflection Loop - Round {current_round} ---")
        
        # Build the contextual history string for the generator's optimization run
        gen_input = f"Issue: {cyber_issue}"
        if feedback_history:
            gen_input += f"\n\nPrevious Iteration Mappings: {latest_mappings}\n\nAuditor Critique to Resolve: {feedback_history}"

        # Step A: Execute the Generator to parse issues and construct mappings
        gen_response = generator_agent(gen_input)
        latest_mappings = gen_response.message
        print(f"[Generator Output]:\n{latest_mappings}")

        # Step B: Pass the generation to the Auditor for verification
        eval_input = f"Original Issue: {cyber_issue}\n\nProposed Mappings:\n{latest_mappings}"
        eval_response = evaluator_agent(eval_input)
        eval_output_raw = eval_response.message
        print(f"[Evaluator Output]:\n{eval_output_raw}")

        # Step C: Parse and sanitize the score payload metrics
        try:
            # Strip markdown fences if appended by the model runtime
            clean_json = eval_output_raw.strip().strip("```json").strip("
```").strip()
            eval_data = json.loads(clean_json)
            
            score = int(eval_data.get("score", 1))
            critique = eval_data.get("critique", "No critique provided.")
        except Exception as e:
            print(f"[JSON Extraction Warning] Failed to parse evaluator string into metrics: {e}")
            score = 1
            critique = "Your response format was invalid JSON. Re-generate adhering precisely to the specified template schema."

        # Step D: Check exit conditions
        if score >= target_score:
            print(f"\n[SUCCESS] Compliance criteria met with an auditor score of {score}/5 in Round {current_round}.")
            try:
                final_mappings_json = json.loads(latest_mappings.strip().strip("```json").strip("```").strip())
            except:
                final_mappings_json = latest_mappings

            return {
                "status": "satisfied",
                "rounds_completed": current_round,
                "auditor_score": score,
                "mappings": final_mappings_json,
                "auditor_critique": critique
            }

        # Progress state management forward to the next refinement pass
        feedback_history = critique
        current_round += 1

    print(f"\n[TIMEOUT] Reached max loop iterations ({max_rounds} rounds) without achieving target baseline.")
    try:
        final_mappings_json = json.loads(latest_mappings.strip().strip("```json").strip("
```").strip())
    except:
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
    Standardized payload consumer for the Amazon Bedrock AgentCore Runtime.
    Expecting API POST body schema: {"prompt": "Vulnerability context description"}
    """
    user_prompt = payload.get("prompt")
    if not user_prompt:
        return {"error": "Missing mandatory 'prompt' key in invocation payload."}
        
    loop_result = run_reflection_loop(user_prompt)
    return {"result": loop_result}

if __name__ == "__main__":
    # Boots server on localhost:8080 during development validation phases
    app.run()