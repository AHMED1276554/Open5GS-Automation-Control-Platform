# ai_agent.py
# Assistant Name: NetSage AI (Ollama Powered)

import os
import glob
import sys
import time
import threading
import requests
import ollama

# Your main Flask orchestrator URL
ORCHESTRATOR_URL = "http://localhost:5000/api/action"
OLLAMA_MODEL = "llama3.1"

# ---------------------------------------------------------
# Loading Spinner Utility for Visual Feedback
# ---------------------------------------------------------
class Spinner:
    def __init__(self, message="Thinking..."):
        self.message = message
        self.stop_running = False
        self.thread = None

    def spinner_task(self):
        chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while not self.stop_running:
            sys.stdout.write(f"\r[⏳ NetSage AI] {chars[i % len(chars)]} {self.message}")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1
        sys.stdout.write("\r" + " " * (len(self.message) + 20) + "\r")
        sys.stdout.flush()

    def __enter__(self):
        self.stop_running = False
        self.thread = threading.Thread(target=self.spinner_task)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_running = True
        if self.thread:
            self.thread.join()

# ---------------------------------------------------------
# Python Functions that Ollama is allowed to trigger
# ---------------------------------------------------------
def check_container_health(component: str = "core", **kwargs) -> str:
    """Check the running status and health of Open5GS Docker containers.
    Args:
        component: The target component or network to check (default is 'core')
    """
    return execute_action("health_check")

def start_core(**kwargs) -> str:
    """Start the 5G core network infrastructure only if not already running."""
    health_status = check_container_health()

    if "OPTIMAL" in health_status or "active running containers" in health_status.lower():
        return "Core is already running. Skipping start step."

    return execute_action("start_core")

def launch_full_network(num_gnbs: int = 2, ues_per_gnb: int = 2, **kwargs) -> str:
    """Launch the entire 5G network sequentially: Start Core -> Start gNBs -> Add UEs."""
    results = []

    print("\n[🚀 NetSage Orchestrator] Step 1: Starting 5G Core Network...")
    results.append(start_core())

    print("[⏳ NetSage Orchestrator] Waiting for 5G core containers to stabilize...")
    time.sleep(5)

    for gnb_id in range(1, int(num_gnbs) + 1):
        print(f"\n[🚀 NetSage Orchestrator] Step 2: Deploying gNodeB {gnb_id}...")
        results.append(start_gnb(gnb_id=gnb_id, tac=1))
        time.sleep(3)

        for u in range(1, int(ues_per_gnb) + 1):
            ue_id = (gnb_id - 1) * int(ues_per_gnb) + u
            print(f"\n[🚀 NetSage Orchestrator] Step 3: Connecting UE {ue_id} to gNodeB {gnb_id}...")
            results.append(add_ue(ue_id=ue_id, target_gnb=gnb_id))
            time.sleep(2)

    return " | ".join(results)

def stop_core() -> str:
    """Stop and tear down the 5G core network."""
    return execute_action("stop_core")

def start_gnb(gnb_id: int, tac: int = 1) -> str:
    """Deploy a new gNodeB (RAN simulator).
    Args:
        gnb_id: The ID of the gNodeB (e.g., 1, 2)
        tac: Tracking Area Code (default is 1)
    """
    return execute_action("start_gnb", {"gnb_id": int(gnb_id), "tac": int(tac)})

def add_ue(ue_id: int, target_gnb: int) -> str:
    """Deploy a User Equipment (UE) connected to a target gNB.
    Args:
        ue_id: The ID of the UE (e.g., 1, 2)
        target_gnb: The gNB ID this UE should connect to
    """
    return execute_action("add_ue", {"ue_id": int(ue_id), "target_gnb": int(target_gnb)})

def get_ue_logs(ue_id: int = 1) -> str:
    """Get the live Docker container logs for a specific User Equipment (UE).
    Args:
        ue_id: The ID of the UE (e.g., 1, 2)
    """
    container_name = f"nr_ue{int(ue_id)}"
    try:
        response = requests.get(
            "http://localhost:5000/api/container-logs",
            params={"container": container_name},
            timeout=5
        )
        if response.ok:
            data = response.json()
            return data.get("logs", "No logs returned.")
        return f"Error: Received status {response.status_code}"
    except Exception as e:
        return f"Failed to connect to orchestrator API: {e}"

def get_gnb_logs(gnb_id: int = 1) -> str:
    """Get the live Docker container logs for a specific gNodeB.
    Args:
        gnb_id: The ID of the gNodeB (e.g., 1, 2)
    """
    container_name = f"nr_gnb{int(gnb_id)}"
    try:
        response = requests.get(
            "http://localhost:5000/api/container-logs",
            params={"container": container_name},
            timeout=5
        )
        if response.ok:
            data = response.json()
            logs = data.get("logs", "")
            return logs if logs.strip() else "CONTAINER_LOGS_EMPTY"
        return f"Error: Received status {response.status_code}"
    except Exception as e:
        return f"Failed to connect to orchestrator API: {e}"

def get_core_logs(component: str = "amf") -> str:
    """Get the live Docker container logs for a core network component (e.g., amf, smf, upf, ausf, udm, udr, nrf, pcf).
    Args:
        component: The core network component name (default is amf)
    """
    try:
        response = requests.get(
            "http://localhost:5000/api/container-logs",
            params={"container": str(component)},
            timeout=5
        )
        if response.ok:
            data = response.json()
            return data.get("logs", "No logs returned.")
        return f"Error: Received status {response.status_code}"
    except Exception as e:
        return f"Failed to connect to orchestrator API: {e}"

def stop_all(**kwargs) -> str:
    """Trigger full cleanup, removing all containers and generated files."""
    return execute_action("stop_all")

def get_logs_or_files(target_name: str = "list") -> str:
    """Inspect local YAML configuration files, report files, logs, or list all available YAML configs in the workspace.
    Args:
        target_name: The filename to inspect (e.g., 'nr-gnb.yaml', 'smf.yaml'), or type 'list' to see all YAML files.
    """
    try:
        if target_name.lower() in ["list", "all", "yamls", "files"]:
            yaml_files = glob.glob("*.yaml")
            return f"Available YAML configuration files in workspace: {yaml_files}"

        if os.path.exists(target_name) and os.path.isfile(target_name):
            with open(target_name, 'r') as f:
                return f.read()[:4000]

        payload = {"action": "get_logs", "target": target_name}
        response = requests.post(ORCHESTRATOR_URL, json=payload, timeout=10)
        if response.ok:
            return str(response.json())
        else:
            return f"Could not find local file or log for '{target_name}'. Use 'list' to see available YAML files."
    except Exception as e:
        return f"Error reading target {target_name}: {e}"

def read_yaml_file(target_name: str) -> str:
    """Read the contents of a YAML configuration file.
    Args:
        target_name: The filename to read (e.g., 'smf.yaml', 'amf.yaml', 'nr-gnb.yaml')
    """
    try:
        if os.path.exists(target_name) and os.path.isfile(target_name):
            with open(target_name, 'r') as f:
                return f.read()[:4000]
        return f"File '{target_name}' not found."
    except Exception as e:
        return f"Error reading file: {e}"

def execute_action(action_name, payload=None):
    """Sends the command payload safely to your main app.py orchestrator."""
    if payload is None:
        payload = {}
    payload["action"] = action_name

    try:
        response = requests.post(ORCHESTRATOR_URL, json=payload, timeout=10)
        if response.ok:
            return f"Success: {response.json()}"
        else:
            return f"Orchestrator error: {response.status_code} - {response.text}"
    except requests.RequestException as e:
        return f"Failed to connect to Flask app at {ORCHESTRATOR_URL}: {e}"

# ---------------------------------------------------------
# Registry Maps (Defined AFTER all functions to prevent NameError)
# ---------------------------------------------------------
TOOL_REGISTRY = {
    "start_core": start_core,
    "stop_core": stop_core,
    "start_gnb": start_gnb,
    "add_ue": add_ue,
    "get_ue_logs": get_ue_logs,
    "get_gnb_logs": get_gnb_logs,
    "get_core_logs": get_core_logs,
    "stop_all": stop_all,
    "get_logs_or_files": get_logs_or_files,
    "read_yaml_file": read_yaml_file,
    "check_container_health": check_container_health,
    "launch_full_network": launch_full_network,
}

available_tools = [
    start_core, stop_core, start_gnb, add_ue,
    get_ue_logs, get_gnb_logs, get_core_logs,
    stop_all, get_logs_or_files, read_yaml_file,
    check_container_health, launch_full_network
]

def run_ai_cli():
    print("==================================================")
    print("🌐 NetSage AI - 5G Standalone Orchestrator (Ollama)")
    print("Make sure your main app.py is running on port 5000!")
    print("Type commands like: 'Check ue 1 logs' or 'Get amf logs'")
    print("Type 'exit' to quit.")
    print("==================================================")

    messages = [
        {
            'role': 'system',
            'content': (
                "You are NetSage AI, a senior 5G network core engineer. "
                "CRITICAL ARCHITECTURE RULE: This is a 100% pure 5G Standalone (SA) Core network using "
                "Open5GS components (AMF, SMF, UPF, UDM, UDR, AUSF, NRF) and UERANSIM. "
                "Never mention 4G/EPC components like HSS or MME, as they do not exist in this environment. "
                "CRITICAL EXECUTION RULE: NEVER hallucinate, simulate, or make up JSON output, tool responses, "
                "or container statuses. If you need to check container health, you MUST call the `check_container_health` "
                "tool and report the exact data returned by the backend. Never invent text formatting pretending an action succeeded "
                "if a tool wasn't executed. "
                "CRITICAL DEPENDENCY RULE: The Core network MUST always be started and fully initialized FIRST "
                "before any gNodeBs (gnb) or UEs can be deployed. Never deploy gNBs or UEs while the core is down. "
                "Always execute deployment steps sequentially: 1) Start Core -> 2) Start gNBs -> 3) Add UEs. "
                "CRITICAL RULE: Never invent errors or reference non-existent components (like AMF[2] or 4G entities). "
                "If logs are normal, explicitly state they are normal. Do not manufacture problems out of routine heartbeat or state-transition logs."
            )
        }
    ]

    while True:
        try:
            user_input = input("\nUser Command > ")
            if user_input.lower() in ['exit', 'quit']:
                print("Exiting NetSage AI.")
                break

            if not user_input.strip():
                continue

            messages.append({'role': 'user', 'content': user_input})

            with Spinner("NetSage AI is analyzing your request..."):
                response = ollama.chat(
                    model=OLLAMA_MODEL,
                    messages=messages,
                    tools=available_tools
                )

            messages.append(response['message'])

            # Handle tool calls if requested by Ollama
            if response.get('message', {}).get('tool_calls'):
                for tool in response['message']['tool_calls']:
                    func_name = tool['function']['name']
                    func_args = tool['function']['arguments'] or {}

                    if func_name in TOOL_REGISTRY:
                        with Spinner(f"Executing tool [{func_name}]..."):
                            tool_result = TOOL_REGISTRY[func_name](**func_args)

                        messages.append({
                            'role': 'tool',
                            'content': str(tool_result),
                            'name': func_name
                        })

                # Follow-up chat call to synthesize the tool results into a friendly answer
                with Spinner("Synthesizing response..."):
                    final_response = ollama.chat(
                        model=OLLAMA_MODEL,
                        messages=messages
                    )

                print(f"\n[NetSage AI]: {final_response['message']['content']}")
                messages.append(final_response['message'])
            else:
                print(f"\n[NetSage AI]: {response['message']['content']}")

        except Exception as e:
            print(f"\n[Error]: {e}")

if __name__ == "__main__":
    run_ai_cli()
