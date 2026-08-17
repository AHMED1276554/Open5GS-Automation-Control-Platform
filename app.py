from flask import Flask, render_template, request, jsonify
from pathlib import Path
import os
import re
import shlex
import subprocess
import threading
import time
import atexit

import requests

app = Flask(__name__)

PROJECT_DIR = Path(__file__).resolve().parent

GENERATED_DIR = PROJECT_DIR / "ueransim" / "generated-ueransim"
AMF_DIR = PROJECT_DIR / "amf"
GENERATED_AMF_DIR = AMF_DIR / "generated-amf"

PROXY_API_URL = os.environ.get(
    "SCTP_PROXY_API_URL",
    "http://localhost:8000"
).rstrip("/")

AMF_SCALE_INTERVAL = 5

network_state = {
    "core_running": False,
    "gnbs": {},
    "ues": {},
    "amfs": {}
}

console_logs = ""
amf_lock = threading.Lock()

# ---------------------------------------------------------
# NetSage AI auto-launch — starts alongside this dashboard so
# there's no separate terminal/process to remember to run.
# ---------------------------------------------------------
netsage_process = None

def start_netsage_ai():
    global netsage_process
    venv_python = PROJECT_DIR / "venv" / "bin" / "python"
    agent_script = PROJECT_DIR / "netsage_agent.py"
    log_path = PROJECT_DIR / "netsage_ai.log"

    if not agent_script.exists():
        print(f"[NetSage AI] {agent_script} not found — skipping auto-start.")
        return

    if not venv_python.exists():
        print(f"[NetSage AI] venv python not found at {venv_python} — skipping auto-start.")
        return

    try:
        log_file = open(log_path, "a")
        netsage_process = subprocess.Popen(
            [str(venv_python), str(agent_script), "--web"],
            cwd=str(PROJECT_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        print(f"[NetSage AI] Started (PID {netsage_process.pid}) — web UI on :5001, logs at {log_path}")
    except Exception as e:
        print(f"[NetSage AI] Failed to start: {e}")

def stop_netsage_ai():
    if netsage_process and netsage_process.poll() is None:
        print("[NetSage AI] Stopping...")
        netsage_process.terminate()
        try:
            netsage_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            netsage_process.kill()

atexit.register(stop_netsage_ai)


def log_msg(message):
    global console_logs

    message = str(message)
    console_logs += message + "\n"
    print(message)


def run_cmd(command):
    log_msg(f"$ {command}")

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True
    )

    if result.stdout:
        log_msg(result.stdout.strip())

    if result.stderr:
        log_msg(f"ERROR: {result.stderr.strip()}")

    return result


def validate_number(value, field_name, minimum=1, maximum=99):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a number.")

    if value < minimum or value > maximum:
        raise ValueError(
            f"{field_name} must be between {minimum} and {maximum}."
        )

    return value


def validation_error(error):
    return jsonify({
        "status": "error",
        "message": str(error)
    }), 400


def read_file(file_path):
    if not file_path.is_file():
        raise FileNotFoundError(
            f"Required file was not found: {file_path}"
        )

    return file_path.read_text(encoding="utf-8")


def docker_container_ip(container_name):
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
            container_name
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return None

    for item in result.stdout.split():
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", item):
            return item

    return None


def wait_for_container_ip(container_name, attempts=20):
    for _ in range(attempts):
        ip_address = docker_container_ip(container_name)

        if ip_address:
            return ip_address

        time.sleep(1)

    return None


def register_amf_with_proxy(ip_address):
    endpoint = f"{PROXY_API_URL}/amfs"

    for attempt in range(1, 6):
        try:
            response = requests.post(
                endpoint,
                json={
                    "ip": ip_address,
                    "port": 38412,
                    "weight": 1
                },
                timeout=4
            )

            if response.ok:
                log_msg(
                    f"AMF {ip_address} registered with SCTP_PROXY."
                )
                return True

            if (
                response.status_code == 400
                and "already exists" in response.text.lower()
            ):
                log_msg(
                    f"AMF {ip_address} is already registered "
                    "with SCTP_PROXY."
                )
                return True

            log_msg(
                f"Proxy registration attempt {attempt} failed: "
                f"{response.status_code} {response.text}"
            )

        except requests.RequestException as error:
            log_msg(
                f"Proxy registration attempt {attempt} failed: {error}"
            )

        time.sleep(1)

    return False


def unregister_amf_from_proxy(ip_address):
    if not ip_address:
        return

    try:
        response = requests.delete(
            f"{PROXY_API_URL}/amfs/{ip_address}",
            timeout=4
        )

        if response.ok or response.status_code == 404:
            log_msg(
                f"AMF {ip_address} removed from SCTP_PROXY."
            )
        else:
            log_msg(
                f"Failed to remove AMF {ip_address} from proxy: "
                f"{response.status_code}"
            )

    except requests.RequestException as error:
        log_msg(
            f"Failed to contact SCTP_PROXY while removing "
            f"{ip_address}: {error}"
        )


def dynamic_amf_compose_content(amf_id):
    container_name = f"amf{amf_id}"
    amf_ip = f"172.22.0.{44 + int(amf_id)}"

    return f"""services:
  {container_name}:
    image: docker_open5gs
    container_name: {container_name}
    env_file:
      - .env
    environment:
      - COMPONENT_NAME={container_name}
      - AMF_CONFIG_FILE={container_name}.yaml
      - AMF_INIT_FILE={container_name}_init.sh
      - NRF_IP=nrf
      - SCP_IP=scp
      - AMF_IP={amf_ip}
    entrypoint:
      - /bin/bash
      - /mnt/generated-amf/{container_name}_init.sh
    volumes:
      - ./amf/generated-amf:/mnt/generated-amf:ro
      - ./log:/open5gs/install/var/log/open5gs
      - /etc/localtime:/etc/localtime:ro
    expose:
      - "38412/sctp"
      - "7777/tcp"
      - "9091/tcp"
    networks:
      default:
        ipv4_address: {amf_ip}

networks:
  default:
    external: true
    name: docker_open5gs_default
"""


def create_dynamic_amf_files(amf_id):
    container_name = f"amf{amf_id}"
    amf_ip = f"172.22.0.{44 + int(amf_id)}"

    GENERATED_AMF_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    source_amf_config = AMF_DIR / "amf.yaml"
    source_amf_init = AMF_DIR / "amf_init.sh"

    amf_config_template = read_file(source_amf_config)
    amf_init_template = read_file(source_amf_init)

    compose_file = PROJECT_DIR / f"{container_name}-deploy.yaml"

    compose_file.write_text(
        dynamic_amf_compose_content(amf_id),
        encoding="utf-8"
    )

    generated_amf_config = amf_config_template

    generated_amf_config = re.sub(
        r"(?m)^(\s*amf_name:\s*)open5gs-amf\d*\s*$",
        lambda match: f"{match.group(1)}open5gs-amf{amf_id}",
        generated_amf_config,
        count=1
    )

    generated_amf_config = re.sub(
        r"(?m)^(\s*path:\s*/open5gs/install/var/log/open5gs/amf)\.log\s*$",
        lambda match: f"{match.group(1)}{amf_id}.log",
        generated_amf_config,
        count=1
    )

    config_file = GENERATED_AMF_DIR / f"{container_name}.yaml"

    config_file.write_text(
        generated_amf_config,
        encoding="utf-8"
    )

    generated_init_script = amf_init_template.replace(
        "cp /mnt/amf/amf.yaml install/etc/open5gs",
        f"cp /mnt/generated-amf/{container_name}.yaml install/etc/open5gs/amf.yaml",
        1
    )

    if f"/mnt/generated-amf/{container_name}.yaml" not in generated_init_script:
        raise ValueError(
            "The source amf_init.sh does not contain the expected "
            "cp /mnt/amf/amf.yaml command."
        )

    init_file = GENERATED_AMF_DIR / f"{container_name}_init.sh"

    init_file.write_text(
        generated_init_script,
        encoding="utf-8"
    )

    os.chmod(init_file, 0o755)

    log_msg(
        f"Generated files for {container_name} with IP {amf_ip}: "
        f"{compose_file.name}, {config_file.name}, {init_file.name}"
    )

    return compose_file, container_name


def provision_dynamic_amf(amf_id):
    with amf_lock:
        amf_key = str(amf_id)
        container_name = f"amf{amf_key}"

        current_amf = network_state["amfs"].get(amf_key)

        if current_amf and current_amf.get("status") == "running":
            log_msg(f"{container_name} is already running.")
            return

        try:
            compose_file, container_name = create_dynamic_amf_files(
                amf_key
            )

            result = run_cmd(
                "docker compose -f "
                f"{shlex.quote(str(compose_file))} up -d"
            )

            if result.returncode != 0:
                network_state["amfs"][amf_key] = {
                    "container": container_name,
                    "ip": "",
                    "status": "error"
                }

                log_msg(f"Failed to create {container_name}.")
                return

            ip_address = wait_for_container_ip(container_name)

            if not ip_address:
                network_state["amfs"][amf_key] = {
                    "container": container_name,
                    "ip": "",
                    "status": "starting"
                }

                log_msg(
                    f"{container_name} was created but Docker IP "
                    "was not available yet."
                )
                return

            register_amf_with_proxy(ip_address)

            network_state["amfs"][amf_key] = {
                "container": container_name,
                "ip": ip_address,
                "status": "running"
            }

            log_msg(
                f"{container_name} is ONLINE with Docker IP "
                f"{ip_address}."
            )

        except Exception as error:
            network_state["amfs"][amf_key] = {
                "container": container_name,
                "ip": "",
                "status": "error"
            }

            log_msg(
                f"Failed to provision {container_name}: {error}"
            )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start-core", methods=["POST"])
@app.route("/api/action", methods=["POST"])
def handle_actions():
    data = request.json or {}
    action = data.get("action")

    if request.path == "/start-core":
        action = "start_core"

    if action == "start_core":
        def background_start():
            log_msg("Starting Open5GS Core...")

            result = run_cmd(
                "docker compose -f sa-deploy.yaml up -d"
            )

            if result.returncode == 0:
                network_state["core_running"] = True

                base_amf_ip = docker_container_ip("amf")

                if not base_amf_ip:
                    base_amf_ip = os.environ.get(
                        "AMF_IP",
                        "172.22.0.10"
                    )

                network_state["amfs"]["0"] = {
                    "container": "amf",
                    "ip": base_amf_ip,
                    "status": "running"
                }

                log_msg("Core is ONLINE.")

        threading.Thread(
            target=background_start,
            daemon=True
        ).start()

        return jsonify({"status": "success"})

    if action == "stop_core":
        def background_stop():
            log_msg("Stopping Open5GS Core...")

            run_cmd(
                "docker compose -f sa-deploy.yaml down"
            )

            network_state["core_running"] = False
            network_state["amfs"].pop("0", None)

            log_msg("Core is OFFLINE.")

        threading.Thread(
            target=background_stop,
            daemon=True
        ).start()

        return jsonify({"status": "success"})

    if action == "start_gnb":
        return add_gnb_logic(
            data.get("gnb_id", 1),
            data.get("tac", 1)
        )

    if action == "stop_gnb":
        try:
            gnb_id = validate_number(
                data.get("gnb_id", 1),
                "gNB ID"
            )
        except ValueError as error:
            return validation_error(error)

        def background_stop_gnb():
            log_msg(f"Stopping and removing gNB {gnb_id}...")

            run_cmd(
                f"docker stop nr_gnb{gnb_id} && docker rm nr_gnb{gnb_id}"
            )

            compose_file = PROJECT_DIR / f"nr-gnb{gnb_id}.yaml"

            if compose_file.is_file():
                compose_file.unlink()

            for generated_file in GENERATED_DIR.glob(
                f"ueransim-gnb{gnb_id}*"
            ):
                if generated_file.is_file():
                    generated_file.unlink()

            network_state["gnbs"].pop(str(gnb_id), None)

            log_msg(f"gNB {gnb_id} removed.")

        threading.Thread(
            target=background_stop_gnb,
            daemon=True
        ).start()

        return jsonify({"status": "success"})

    if action == "add_ue":
        return add_ue_logic(
            data.get("ue_id", 1),
            data.get("target_gnb", 1),
            data.get("sst", 1)
        )

    if action == "stop_ue":
        try:
            ue_id = validate_number(
                data.get("ue_id", 1),
                "UE ID"
            )
        except ValueError as error:
            return validation_error(error)

        def background_stop_ue():
            log_msg(f"Stopping and removing UE {ue_id}...")

            run_cmd(
                f"docker stop nr_ue{ue_id} && docker rm nr_ue{ue_id}"
            )

            compose_file = PROJECT_DIR / f"nr-ue{ue_id}.yaml"

            if compose_file.is_file():
                compose_file.unlink()

            for generated_file in GENERATED_DIR.glob(
                f"ueransim-ue{ue_id}*"
            ):
                if generated_file.is_file():
                    generated_file.unlink()

            network_state["ues"].pop(str(ue_id), None)

            log_msg(f"UE {ue_id} removed.")

        threading.Thread(
            target=background_stop_ue,
            daemon=True
        ).start()

        return jsonify({"status": "success"})

    if action == "add_amf":
        return add_amf_logic(data.get("amf_id", 1))

    if action == "handover_ue":
        return add_ue_logic(
            data.get("ue_id", 1),
            data.get("target_gnb", 1),
            data.get("sst", 1)
        )

    if action == "ping_dynamic_ue":
        try:
            ue_id = validate_number(
                data.get("ue_num", 1),
                "UE ID"
            )
        except ValueError as error:
            return validation_error(error)

        target_ip = data.get("target_ip", "8.8.8.8")
        if not re.fullmatch(r"[\w\.-]+", target_ip):
            target_ip = "8.8.8.8"

        result = run_cmd(
            f"docker exec -t nr_ue{ue_id} ping -I uesimtun0 -c 4 {target_ip}"
        )

        if result.returncode == 0:
            return jsonify({
                "status": "success",
                "message": "Ping success"
            })

        return jsonify({
            "status": "error",
            "message": "Ping failed"
        })

    if action == "stop_all":
        return cleanup_logic()

    return jsonify({
        "status": "error",
        "message": "Unknown action"
    }), 400


@app.route("/add-amf", methods=["POST"])
def add_amf_api():
    data = request.json or {}

    return add_amf_logic(
        data.get("amf_id", 1)
    )


def add_amf_logic(amf_id):
    try:
        amf_id = validate_number(
            amf_id,
            "AMF ID"
        )
    except ValueError as error:
        return validation_error(error)

    threading.Thread(
        target=provision_dynamic_amf,
        args=(amf_id,),
        daemon=True
    ).start()

    return jsonify({"status": "success"})


@app.route("/add-gnb", methods=["POST"])
def add_gnb_api():
    data = request.json or {}

    return add_gnb_logic(
        data.get("gnb_id", 1),
        data.get("tac", 1)
    )


def add_gnb_logic(gnb_id, tac=1):
    try:
        gnb_id = validate_number(
            gnb_id,
            "gNB ID"
        )

        tac = validate_number(
            tac,
            "TAC",
            1,
            10
        )

    except ValueError as error:
        return validation_error(error)

    gnb_key = str(gnb_id)
    component_name = f"ueransim-gnb{gnb_key}"

    def background_gnb():
        GENERATED_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        compose_file = PROJECT_DIR / f"nr-gnb{gnb_key}.yaml"

        compose_content = f"""services:
  nr_gnb{gnb_key}:
    image: docker_ueransim
    container_name: nr_gnb{gnb_key}
    stdin_open: true
    tty: true
    volumes:
      - ./ueransim/generated-ueransim:/mnt/ueransim
      - /etc/localtime:/etc/localtime:ro
    environment:
      - COMPONENT_NAME={component_name}
      - MNC=01
      - MCC=001
      - TAC={tac}
      - NR_GNB_IP=AUTO_IP
      - AMF_IP=${{SCTP_PROXY_IP}}
    expose:
      - "38412/sctp"
      - "2152/udp"
      - "4997/udp"
    cap_add:
      - NET_ADMIN
    privileged: true
    networks:
      - default

networks:
  default:
    external: true
    name: docker_open5gs_default
"""

        compose_file.write_text(
            compose_content,
            encoding="utf-8"
        )

        init_script_path = GENERATED_DIR / f"{component_name}_init.sh"

        init_script_content = """#!/bin/bash
export IP_ADDR=$(awk 'END{print $1}' /etc/hosts)

cp /mnt/ueransim/${COMPONENT_NAME}.yaml /UERANSIM/config/${COMPONENT_NAME}.yaml

sed -i 's|MNC|'$MNC'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|MCC|'$MCC'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|TAC|'$TAC'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|NR_GNB_IP|'$IP_ADDR'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|AMF_IP|'$AMF_IP'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml

./nr-gnb -c ../config/${COMPONENT_NAME}.yaml &
exec bash $@
"""

        init_script_path.write_text(
            init_script_content,
            encoding="utf-8"
        )

        os.chmod(init_script_path, 0o755)

        gnb_config_file = GENERATED_DIR / f"{component_name}.yaml"

        gnb_config_content = f"""mcc: 'MCC'
mnc: 'MNC'
nci: '0x0000000{gnb_id:02x}'
idLength: 32
tac: TAC
linkIp: NR_GNB_IP
ngapIp: NR_GNB_IP
gtpIp: NR_GNB_IP
amfConfigs:
  - address: AMF_IP
    port: 38412
slices:
  - sst: 1
  - sst: 2
  - sst: 3
  - sst: 4
ignoreStreamIds: true
"""

        gnb_config_file.write_text(
            gnb_config_content,
            encoding="utf-8"
        )

        result = run_cmd(
            "docker compose -f "
            f"{shlex.quote(str(compose_file))} up -d"
        )

        if result.returncode == 0:
            ip_address = wait_for_container_ip(f"nr_gnb{gnb_key}")

            network_state["gnbs"][gnb_key] = {
                "status": "running",
                "tac": tac,
                "ip": ip_address or "Pending..."
            }

            log_msg(
                f"gNodeB {gnb_key} with TAC {tac} is ONLINE "
                "through SCTP_PROXY."
            )

    threading.Thread(
        target=background_gnb,
        daemon=True
    ).start()

    return jsonify({"status": "success"})


@app.route("/add-ue", methods=["POST"])
def add_ue_api():
    data = request.json or {}

    return add_ue_logic(
        data.get("ue_id", 1),
        data.get("target_gnb", 1),
        data.get("sst", 1)
    )


def add_ue_logic(ue_id, target_gnb, sst=1):
    try:
        ue_id = validate_number(
            ue_id,
            "UE ID"
        )

        target_gnb = validate_number(
            target_gnb,
            "Target gNB ID"
        )

        sst = validate_number(
            sst,
            "SST",
            1,
            4
        )

    except ValueError as error:
        return validation_error(error)

    ue_key = str(ue_id)
    target_gnb_key = str(target_gnb)
    component_name = f"ueransim-ue{ue_key}"

    def background_ue():
        try:
            GENERATED_DIR.mkdir(
                parents=True,
                exist_ok=True
            )

            padded_id = f"{ue_id:010d}"
            imsi = f"00101{padded_id}"
            imei = f"356938035643{ue_id:03d}"
            imei_sv = f"356938035643{ue_id:03d}0"
            key = f"8baf473f2f8fd09487cccbd7097c0{ue_id:03d}"
            op = f"8E27B6AF0E692E750F32667A3B146{ue_id:03d}"

            compose_file = PROJECT_DIR / f"nr-ue{ue_key}.yaml"

            compose_content = f"""services:
  nr_ue{ue_key}:
    image: docker_ueransim
    container_name: nr_ue{ue_key}
    stdin_open: true
    tty: true
    volumes:
      - ./ueransim/generated-ueransim:/mnt/ueransim
      - /etc/localtime:/etc/localtime:ro
    environment:
      - COMPONENT_NAME={component_name}
      - MNC=01
      - MCC=001
      - UE1_KI={key}
      - UE1_OP={op}
      - UE1_AMF=8000
      - UE1_IMEI={imei}
      - UE1_IMEISV={imei_sv}
      - UE1_IMSI={imsi}
      - NR_GNB_IP=nr_gnb{target_gnb_key}
    expose:
      - "4997/udp"
    cap_add:
      - NET_ADMIN
    privileged: true
    networks:
      - default

networks:
  default:
    external: true
    name: docker_open5gs_default
"""

            compose_file.write_text(
                compose_content,
                encoding="utf-8"
            )

            init_script_path = GENERATED_DIR / f"{component_name}_init.sh"

            init_script_content = """#!/bin/bash
export IP_ADDR=$(awk 'END{print $1}' /etc/hosts)

cp /mnt/ueransim/${COMPONENT_NAME}.yaml /UERANSIM/config/${COMPONENT_NAME}.yaml

sed -i 's|MNC|'$MNC'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|MCC|'$MCC'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|UE1_KI|'$UE1_KI'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|UE1_OP|'$UE1_OP'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|UE1_AMF|'$UE1_AMF'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|UE1_IMEISV|'$UE1_IMEISV'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|UE1_IMEI|'$UE1_IMEI'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|UE1_IMSI|'$UE1_IMSI'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml
sed -i 's|NR_GNB_IP|'$NR_GNB_IP'|g' /UERANSIM/config/${COMPONENT_NAME}.yaml

./nr-ue -c ../config/${COMPONENT_NAME}.yaml &
exec bash $@
"""

            init_script_path.write_text(
                init_script_content,
                encoding="utf-8"
            )

            os.chmod(init_script_path, 0o755)

            ue_config_file = GENERATED_DIR / f"{component_name}.yaml"

            ue_config_content = f"""supi: 'imsi-UE1_IMSI'
mcc: 'MCC'
mnc: 'MNC'
key: 'UE1_KI'
op: 'UE1_OP'
opType: 'OPC'
amf: 'UE1_AMF'
imei: 'UE1_IMEI'
imeiSv: 'UE1_IMEISV'
gnbSearchList:
  - NR_GNB_IP
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: {sst}
configured-nssai:
  - sst: {sst}
default-nssai:
  - sst: {sst}
integrity:
  IA1: true
  IA2: true
  IA3: true
ciphering:
  EA1: true
  EA2: true
  EA3: true
integrityMaxRate:
  uplink: 'full'
  downlink: 'full'
"""

            ue_config_file.write_text(
                ue_config_content,
                encoding="utf-8"
            )

            run_cmd(
                "docker exec webui misc/db/open5gs-dbctl "
                f"add_ue_with_apn {imsi} {key} {op} internet"
            )

            if sst == 1:
                run_cmd(
                    f'docker exec mongo mongosh open5gs --eval \'db.subscribers.updateOne({{ imsi: "{imsi}" }}, {{ $set: {{ "slice.0.sst": 1, "slice.0.session.0.qos.index": 9, "slice.0.session.0.qos.arp.priority_level": 8, "slice.0.session.0.qos.arp.pre_emption_capability": 1, "slice.0.session.0.qos.arp.pre_emption_vulnerability": 2 }} }})\''
                )
            elif sst == 2:
                run_cmd(
                    f'docker exec mongo mongosh open5gs --eval \'db.subscribers.updateOne({{ imsi: "{imsi}" }}, {{ $set: {{ "slice.0.sst": 2, "slice.0.session.0.qos.index": 80, "slice.0.session.0.qos.arp.priority_level": 1, "slice.0.session.0.qos.arp.pre_emption_capability": 1, "slice.0.session.0.qos.arp.pre_emption_vulnerability": 1 }} }})\''
                )
            elif sst == 3:
                run_cmd(
                    f'docker exec mongo mongosh open5gs --eval \'db.subscribers.updateOne({{ imsi: "{imsi}" }}, {{ $set: {{ "slice.0.sst": 3, "slice.0.session.0.qos.index": 7, "slice.0.session.0.qos.arp.priority_level": 12, "slice.0.session.0.qos.arp.pre_emption_capability": 1, "slice.0.session.0.qos.arp.pre_emption_vulnerability": 2 }} }})\''
                )
            elif sst == 4:
                run_cmd(
                    f'docker exec mongo mongosh open5gs --eval \'db.subscribers.updateOne({{ imsi: "{imsi}" }}, {{ $set: {{ "slice.0.sst": 4, "slice.0.session.0.qos.index": 65, "slice.0.session.0.qos.arp.priority_level": 3, "slice.0.session.0.qos.arp.pre_emption_capability": 1, "slice.0.session.0.qos.arp.pre_emption_vulnerability": 2 }} }})\''
                )
            else:
                run_cmd(
                    f'docker exec mongo mongosh open5gs --eval \'db.subscribers.updateOne({{ imsi: "{imsi}" }}, {{ $set: {{ "slice.0.sst": {sst} }} }})\''
                )

            result = run_cmd(
                "docker compose -f "
                f"{shlex.quote(str(compose_file))} up -d"
            )

            if result.returncode != 0:
                log_msg(f"Failed to create UE {ue_key}.")
                return

            ip_address = wait_for_container_ip(f"nr_ue{ue_key}")

            network_state["ues"][ue_key] = {
                "gnb": target_gnb_key,
                "status": "running",
                "ip": ip_address or "Pending...",
                "sst": sst
            }

            log_msg(f"UE {ue_key} is ONLINE.")

            active_ue_count = len(network_state["ues"])

            if active_ue_count > 0 and active_ue_count % AMF_SCALE_INTERVAL == 0:
                amf_id = active_ue_count // AMF_SCALE_INTERVAL

                log_msg(
                    f"Total active UEs reached {active_ue_count}. Triggered AMF scale-out: "
                    f"creating amf{amf_id}."
                )

                provision_dynamic_amf(amf_id)

        except Exception as error:
            log_msg(f"Failed to create UE {ue_key}: {error}")

    threading.Thread(
        target=background_ue,
        daemon=True
    ).start()

    return jsonify({"status": "success"})


@app.route("/cleanup", methods=["POST"])
def cleanup_api():
    return cleanup_logic()


def delete_files_only(directory):
    if not directory.is_dir():
        return

    for file_path in directory.iterdir():
        if file_path.is_file() or file_path.is_symlink():
            file_path.unlink()


def cleanup_logic():
    def background_cleanup():
        log_msg("Initiating full cleanup...")

        for amf in list(network_state["amfs"].values()):
            if amf.get("container") != "amf":
                unregister_amf_from_proxy(
                    amf.get("ip")
                )

        run_cmd(
            "docker rm -f $(docker ps -aq) 2>/dev/null || true"
        )

        generated_compose_pattern = re.compile(
            r"^(?:nr-gnb|nr-ue)\d+\.yaml$|^amf\d+-deploy\.yaml$"
        )

        for file_path in PROJECT_DIR.iterdir():
            if (
                file_path.is_file()
                and generated_compose_pattern.fullmatch(file_path.name)
            ):
                file_path.unlink()

        delete_files_only(GENERATED_DIR)
        delete_files_only(GENERATED_AMF_DIR)

        GENERATED_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        GENERATED_AMF_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        network_state["gnbs"].clear()
        network_state["ues"].clear()
        network_state["amfs"].clear()
        network_state["core_running"] = False

        log_msg("Cleanup completed successfully.")

    threading.Thread(
        target=background_cleanup,
        daemon=True
    ).start()

    return jsonify({"status": "success"})


@app.route("/api/container-logs", methods=["GET"])
def container_logs():
    container = request.args.get("container", "")

    if not re.fullmatch(
        r"(?:amf\d*|nr_gnb\d+|nr_ue\d+)",
        container
    ):
        return jsonify({
            "logs": "Invalid container name."
        }), 400

    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "150", container],
            capture_output=True,
            text=True
        )

        return jsonify({
            "logs": result.stdout + result.stderr
        })

    except Exception as error:
        return jsonify({
            "logs": f"Error fetching logs: {error}"
        })


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "core": "ONLINE" if network_state["core_running"] else "OFFLINE",
        "gnbs": network_state["gnbs"],
        "ues": network_state["ues"],
        "amfs": network_state["amfs"],
        "console_output": console_logs
    })


if __name__ == "__main__":
    GENERATED_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    GENERATED_AMF_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Flask's debug reloader re-executes this whole file twice (once as a
    # "watcher" process, once as the real server). This guard makes sure
    # NetSage AI only launches once, in the real server process, so it
    # doesn't try to start twice and fight over port 5001.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_netsage_ai()

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000,
        threaded=True
    )
