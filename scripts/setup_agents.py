"""
Foundry Workflow — Automated Setup Script
==========================================
Creates all agents, uploads files, creates vector stores, and prints
IDs needed for the workflow. Run once to bootstrap your Foundry project.

Usage:
    1. Copy .env.example → .env and fill in FOUNDRY_PROJECT_ENDPOINT,
       FOUNDRY_MODEL_DEPLOYMENT_NAME, and BING_CONNECTION_NAME.
    2. Run: az login
    3. Run: pip install azure-ai-projects azure-identity python-dotenv
    4. Run: python scripts/setup_agents.py

The script will:
  - Upload mock-data files (CSV, MD) via the OpenAI Files API
  - Create two vector stores (policy + contract templates)
  - Create all 5 agents with the correct tools attached
  - Print a summary of created resource IDs
"""

import os
import sys
import json
from pathlib import Path

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    CodeInterpreterTool,
    AutoCodeInterpreterToolParam,
    FileSearchTool,
    PromptAgentDefinition,
)

# ── Load environment ──────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

PROJECT_ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
MODEL_NAME = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o")
BING_CONNECTION_NAME = os.environ.get("BING_CONNECTION_NAME", "")

if not PROJECT_ENDPOINT or "YOUR_RESOURCE" in PROJECT_ENDPOINT:
    print("ERROR: Set FOUNDRY_PROJECT_ENDPOINT in .env before running this script.")
    sys.exit(1)

MOCK_DATA = ROOT_DIR / "mock-data"
AGENTS_DIR = ROOT_DIR / "agents"

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_yaml_instructions(yaml_path: Path) -> str:
    """Extract the instructions block from an agent YAML file."""
    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    capture = False
    result = []
    for line in lines:
        if line.strip().startswith("instructions:"):
            capture = True
            # Handle inline value after 'instructions: |'
            continue
        if capture:
            if line and not line[0].isspace() and not line.startswith(" "):
                break
            result.append(line)
    return "\n".join(result).strip()


def heading(text: str) -> None:
    print(f"\n{'─'*60}\n  {text}\n{'─'*60}")


def find_existing_file(openai_client, filename: str) -> str | None:
    """Return the file ID if a file with this name already exists, else None."""
    for f in openai_client.files.list(purpose="assistants"):
        if f.filename == filename:
            return f.id
    return None


def find_existing_vector_store(openai_client, name: str):
    """Return the vector store object if one with this name exists, else None."""
    for vs in openai_client.vector_stores.list():
        if vs.name == name:
            return vs
    return None


def agent_exists(project_client, agent_name: str) -> bool:
    """Return True if at least one version of this agent already exists."""
    try:
        versions = project_client.agents.list_versions(agent_name=agent_name, limit=1)
        return len(versions.data) > 0
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    heading("Connecting to Foundry project")
    project = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )
    openai = project.get_openai_client()
    print(f"  Endpoint: {PROJECT_ENDPOINT}")

    # ── Upload files ──────────────────────────────────────────────────────────
    heading("Uploading mock-data files")

    files_to_upload = {
        "vendor_financials.csv": MOCK_DATA / "vendor_financials.csv",
        "procurement_policy.md": MOCK_DATA / "procurement_policy.md",
        "vendor_blacklist.md": MOCK_DATA / "vendor_blacklist.md",
        "standard_vendor_contract.md": MOCK_DATA / "contract_templates" / "standard_vendor_contract.md",
    }

    uploaded = {}
    for label, path in files_to_upload.items():
        existing_id = find_existing_file(openai, label)
        if existing_id:
            uploaded[label] = existing_id
            print(f"  ● {label} → {existing_id} (already exists)")
        else:
            with open(path, "rb") as f:
                result = openai.files.create(purpose="assistants", file=f)
            uploaded[label] = result.id
            print(f"  ✓ {label} → {result.id}")

    # ── Create vector stores ──────────────────────────────────────────────────
    heading("Creating vector stores")

    # Policy vector store (Agent 2)
    policy_vs = find_existing_vector_store(openai, "contoso-policy-store")
    if policy_vs:
        print(f"  ● Policy vector store: {policy_vs.id} (already exists)")
    else:
        policy_vs = openai.vector_stores.create(name="contoso-policy-store")
        for fname in ["procurement_policy.md", "vendor_blacklist.md"]:
            openai.vector_stores.files.create(
                vector_store_id=policy_vs.id,
                file_id=uploaded[fname],
            )
        print(f"  ✓ Policy vector store: {policy_vs.id}")
        print(f"    Files: procurement_policy.md, vendor_blacklist.md")

    # Contract template vector store (Agent 5)
    contract_vs = find_existing_vector_store(openai, "contoso-contract-templates")
    if contract_vs:
        print(f"  ● Contract template vector store: {contract_vs.id} (already exists)")
    else:
        contract_vs = openai.vector_stores.create(name="contoso-contract-templates")
        openai.vector_stores.files.create(
            vector_store_id=contract_vs.id,
            file_id=uploaded["standard_vendor_contract.md"],
        )
        print(f"  ✓ Contract template vector store: {contract_vs.id}")
        print(f"    Files: standard_vendor_contract.md")

    # ── Resolve Bing connection ───────────────────────────────────────────────
    heading("Resolving Bing Search connection")
    bing_connection_id = ""
    if BING_CONNECTION_NAME:
        try:
            conn = project.connections.get(name=BING_CONNECTION_NAME)
            bing_connection_id = conn.id
            print(f"  ✓ Bing connection: {bing_connection_id}")
        except Exception as e:
            print(f"  ⚠ Could not resolve Bing connection '{BING_CONNECTION_NAME}': {e}")
            print("    Agent 1 will be created without Bing Grounding.")
            print("    You can add the Bing tool manually in the portal later.")
    else:
        print("  ⚠ BING_CONNECTION_NAME not set. Skipping Bing Grounding tool.")
        print("    You can add the Bing tool manually in the portal later.")

    # ── Create agents ─────────────────────────────────────────────────────────
    heading("Creating agents")
    agents_created = {}
    agents_skipped = {}

    # --- Agent 1: Market Intelligence (Bing Grounding) ---
    if agent_exists(project, "market-intelligence-agent"):
        agents_skipped["market-intelligence-agent"] = True
        print(f"  ● market-intelligence-agent (already exists)")
    else:
        agent1_instructions = read_yaml_instructions(AGENTS_DIR / "01-market-intelligence-agent.yaml")
        agent1_tools = []
        if bing_connection_id:
            agent1_tools.append({
                "type": "bing_grounding",
                "bing_grounding": {
                    "search_configurations": [
                        {"project_connection_id": bing_connection_id}
                    ]
                },
            })

        agent1 = project.agents.create_version(
            agent_name="market-intelligence-agent",
            definition=PromptAgentDefinition(
                model=MODEL_NAME,
                instructions=agent1_instructions,
                tools=agent1_tools,
            ),
            description="Researches vendor reputation via real-time web search (Bing Grounding).",
        )
        agents_created["market-intelligence-agent"] = agent1
        print(f"  ✓ market-intelligence-agent (v{agent1.version})")

    # --- Agent 2: Policy & Compliance (File Search) ---
    if agent_exists(project, "policy-compliance-agent"):
        agents_skipped["policy-compliance-agent"] = True
        print(f"  ● policy-compliance-agent (already exists)")
    else:
        agent2_instructions = read_yaml_instructions(AGENTS_DIR / "02-policy-compliance-agent.yaml")
        agent2 = project.agents.create_version(
            agent_name="policy-compliance-agent",
            definition=PromptAgentDefinition(
                model=MODEL_NAME,
                instructions=agent2_instructions,
                tools=[FileSearchTool(vector_store_ids=[policy_vs.id])],
            ),
            description="Checks vendor against internal procurement policies and blacklist.",
        )
        agents_created["policy-compliance-agent"] = agent2
        print(f"  ✓ policy-compliance-agent (v{agent2.version})")

    # --- Agent 3: Financial Risk (Code Interpreter) ---
    if agent_exists(project, "financial-risk-agent"):
        agents_skipped["financial-risk-agent"] = True
        print(f"  ● financial-risk-agent (already exists)")
    else:
        agent3_instructions = read_yaml_instructions(AGENTS_DIR / "03-financial-risk-agent.yaml")
        agent3 = project.agents.create_version(
            agent_name="financial-risk-agent",
            definition=PromptAgentDefinition(
                model=MODEL_NAME,
                instructions=agent3_instructions,
                tools=[
                    CodeInterpreterTool(
                        container=AutoCodeInterpreterToolParam(
                            file_ids=[uploaded["vendor_financials.csv"]]
                        )
                    )
                ],
            ),
            description="Performs financial ratio analysis and generates risk charts.",
        )
        agents_created["financial-risk-agent"] = agent3
        print(f"  ✓ financial-risk-agent (v{agent3.version})")

    # --- Agent 4: Risk Scoring (Code Interpreter — executes scoring algorithm) ---
    if agent_exists(project, "risk-scoring-agent"):
        agents_skipped["risk-scoring-agent"] = True
        print(f"  ● risk-scoring-agent (already exists)")
    else:
        agent4_instructions = read_yaml_instructions(AGENTS_DIR / "04-risk-scoring-agent.yaml")

        agent4 = project.agents.create_version(
            agent_name="risk-scoring-agent",
            definition=PromptAgentDefinition(
                model=MODEL_NAME,
                instructions=agent4_instructions,
                tools=[CodeInterpreterTool()],
            ),
            description="Aggregates signals from all agents into a composite risk score.",
        )
        agents_created["risk-scoring-agent"] = agent4
        print(f"  ✓ risk-scoring-agent (v{agent4.version})")

    # --- Agent 5: Contract Drafting (File Search) ---
    if agent_exists(project, "contract-drafting-agent"):
        agents_skipped["contract-drafting-agent"] = True
        print(f"  ● contract-drafting-agent (already exists)")
    else:
        agent5_instructions = read_yaml_instructions(AGENTS_DIR / "05-contract-drafting-agent.yaml")
        agent5 = project.agents.create_version(
            agent_name="contract-drafting-agent",
            definition=PromptAgentDefinition(
                model=MODEL_NAME,
                instructions=agent5_instructions,
                tools=[FileSearchTool(vector_store_ids=[contract_vs.id])],
            ),
            description="Generates populated vendor contracts from templates.",
        )
        agents_created["contract-drafting-agent"] = agent5
        print(f"  ✓ contract-drafting-agent (v{agent5.version})")

    # ── Summary ───────────────────────────────────────────────────────────────
    heading("Setup complete!")
    print()
    new_agents = len(agents_created)
    skipped_agents = len(agents_skipped)
    print("  Created resources:")
    print(f"    Files:                   {len(uploaded)} (new or reused)")
    print(f"    Vector stores:           2 (new or reused)")
    print(f"    Agents created:          {new_agents}")
    if skipped_agents:
        print(f"    Agents skipped:          {skipped_agents} (already existed)")
    print()
    print("  Resource IDs:")
    print(f"    Policy vector store:     {policy_vs.id}")
    print(f"    Contract vector store:   {contract_vs.id}")
    print(f"    Financials file:         {uploaded['vendor_financials.csv']}")
    if bing_connection_id:
        print(f"    Bing connection:         {bing_connection_id}")
    print()
    print("  Next step:")
    print("    1. Open the Foundry portal → Workflows → Create new → Sequential")
    print("    2. Build the workflow using the visual canvas (see workflow/ for reference)")
    print("       OR toggle the YAML view and paste the content of")
    print("       workflow/vendor-due-diligence-workflow.yaml")
    print("    3. Assign each 'Invoke agent' node to the agents created above")
    print("    4. Save and click 'Run Workflow' to test")
    print()


if __name__ == "__main__":
    main()
