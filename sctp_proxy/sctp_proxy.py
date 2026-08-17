import asyncio
import logging
import socket
from typing import Dict, List, Optional, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("SCTP_PROXY")

app = FastAPI(
    title="SCTP_PROXY - 5G Core SCTP Proxy",
    version="1.2"
)

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 38412
SCTP_PROTOCOL = getattr(socket, "IPPROTO_SCTP", 132)


class AMFNode(BaseModel):
    ip: str
    port: int = 38412
    weight: int = 1


# الـAMF الأساسي
upstream_amfs: List[AMFNode] = [
    AMFNode(ip="172.22.0.10", port=38412)
]

active_sessions: Dict[str, Tuple[str, int]] = {}


class ProxyServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def select_amf(self, session_key: str) -> Optional[Tuple[str, int]]:
        if not upstream_amfs:
            return None

        # 1. الحفاظ على الجلسات القديمة مرتبطة بنفس الـ AMF الخاص بها طالما أنه ما زال موجوداً
        if session_key in active_sessions:
            selected_amf = active_sessions[session_key]

            if any(
                amf.ip == selected_amf[0] and amf.port == selected_amf[1]
                for amf in upstream_amfs
            ):
                return selected_amf

        # 2. الترافيك والاتصالات الجديدة توجّه حصرياً إلى أحدث AMF تمت إضافتها (آخر عنصر في القائمة)
        amf_node = upstream_amfs[-1]

        selected_amf = (amf_node.ip, amf_node.port)
        active_sessions[session_key] = selected_amf

        logger.info(
            f"New session {session_key} routed to the latest AMF -> "
            f"{selected_amf[0]}:{selected_amf[1]}"
        )

        return selected_amf

    async def close_writer(self, writer):
        if writer is None:
            return

        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def handle_gnb_client(self, reader, writer):
        peername = writer.get_extra_info("peername")
        session_key = str(peername[0]) if peername else "unknown_gnb"

        logger.info(
            f"[SCTP_PROXY] New gNodeB connected from {peername}"
        )

        amf_endpoint = self.select_amf(session_key)

        if not amf_endpoint:
            logger.error("No available AMF found.")
            await self.close_writer(writer)
            return

        logger.info(
            f"Routing gNodeB {session_key} to AMF -> "
            f"{amf_endpoint[0]}:{amf_endpoint[1]}"
        )

        try:
            amf_reader, amf_writer = await asyncio.open_connection(
                host=amf_endpoint[0],
                port=amf_endpoint[1],
                family=socket.AF_INET,
                proto=SCTP_PROTOCOL,
                flags=socket.AI_NUMERICHOST
            )

        except Exception as error:
            logger.error(
                f"Failed to connect to AMF {amf_endpoint}: {error}"
            )
            await self.close_writer(writer)
            return

        async def forward(source_reader, destination_writer, direction):
            try:
                while True:
                    data = await source_reader.read(65535)

                    if not data:
                        break

                    destination_writer.write(data)
                    await destination_writer.drain()

            except Exception as error:
                logger.warning(
                    f"{direction} forwarding stopped: {error}"
                )

            finally:
                await self.close_writer(destination_writer)

        gnb_to_amf_task = asyncio.create_task(
            forward(reader, amf_writer, "gNodeB -> AMF")
        )

        amf_to_gnb_task = asyncio.create_task(
            forward(amf_reader, writer, "AMF -> gNodeB")
        )

        await asyncio.gather(
            gnb_to_amf_task,
            amf_to_gnb_task,
            return_exceptions=True
        )

        logger.info(f"Session ended for gNodeB: {session_key}")

    async def start_proxy(self):
        server_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
            SCTP_PROTOCOL
        )

        server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        server_socket.bind((self.host, self.port))
        server_socket.listen(128)
        server_socket.setblocking(False)

        server = await asyncio.start_server(
            self.handle_gnb_client,
            sock=server_socket
        )

        logger.info(
            f"SCTP_PROXY started listening on "
            f"{self.host}:{self.port}"
        )

        async with server:
            await server.serve_forever()


proxy_instance = ProxyServer(LISTEN_HOST, LISTEN_PORT)


WEB_UI_HTML = """
<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SCTP PROXY Dashboard</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='24' height='24' fill='none' stroke='%23cc00ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='2' y='2' width='20' height='8' rx='2' ry='2'></rect><rect x='2' y='14' width='20' height='8' rx='2' ry='2'></rect><line x1='6' y1='6' x2='6.01' y2='6'></line><line x1='6' y1='18' x2='6.01' y2='18'></line><line x1='10' y1='6' x2='14' y2='6'></line><line x1='10' y1='18' x2='14' y2='18'></line></svg>">
    <style>
        body {
            background-color: #0d0d0d; /* Deep Matte Black */
            color: #e0b3ff;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
        }
        h1 {
            color: #cc00ff; /* Neon Purple */
            text-shadow: 0 0 10px #cc00ff, 0 0 20px #cc00ff, 0 0 40px #cc00ff;
            margin-top: 40px;
            font-size: 3rem;
            letter-spacing: 2px;
        }
        .container {
            background-color: #1a1a1a;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 0 20px rgba(204, 0, 255, 0.4);
            width: 90%;
            max-width: 650px;
            margin-top: 20px;
            border: 1px solid #33004d;
        }
        .info-box {
            margin-bottom: 25px;
            padding: 20px;
            border: 1px solid #cc00ff;
            border-radius: 8px;
            background-color: #050505;
            box-shadow: inset 0 0 10px rgba(204, 0, 255, 0.1);
        }
        .info-box h3 {
            margin-top: 0;
            color: #df80ff;
            text-transform: uppercase;
            font-size: 1.1rem;
            border-bottom: 1px solid #33004d;
            padding-bottom: 10px;
        }
        .proxy-ip {
            font-size: 1.4rem;
            font-weight: bold;
            color: #ffffff;
            text-shadow: 0 0 8px #cc00ff;
        }
        ul {
            list-style-type: none;
            padding: 0;
            margin: 0;
            max-height: 250px;
            overflow-y: auto;
        }
        /* Custom scrollbar */
        ul::-webkit-scrollbar { width: 8px; }
        ul::-webkit-scrollbar-track { background: #050505; }
        ul::-webkit-scrollbar-thumb { background: #cc00ff; border-radius: 4px; }
        
        li {
            background: #1a0026;
            margin: 8px 0;
            padding: 12px 15px;
            border-left: 5px solid #cc00ff;
            font-family: 'Courier New', Courier, monospace;
            font-weight: bold;
            font-size: 1.1rem;
            color: #ffffff;
            border-radius: 4px;
            display: flex;
            align-items: center;
        }
        li::before {
            content: "•";
            color: #cc00ff;
            font-size: 1.5rem;
            margin-right: 15px;
            text-shadow: 0 0 5px #cc00ff;
        }
        .input-group {
            display: flex;
            gap: 15px;
            margin-top: 15px;
        }
        input[type="text"] {
            flex-grow: 1;
            background-color: #000000;
            border: 1px solid #cc00ff;
            color: #ffffff;
            padding: 12px 15px;
            border-radius: 6px;
            font-family: 'Courier New', Courier, monospace;
            font-size: 1rem;
            outline: none;
            transition: box-shadow 0.3s;
        }
        input[type="text"]:focus {
            box-shadow: 0 0 10px rgba(204, 0, 255, 0.5);
        }
        button {
            background-color: #cc00ff;
            color: #ffffff;
            border: none;
            padding: 12px 25px;
            cursor: pointer;
            border-radius: 6px;
            font-weight: bold;
            font-size: 1rem;
            box-shadow: 0 0 10px #cc00ff;
            transition: all 0.3s ease;
            text-transform: uppercase;
        }
        button:hover {
            background-color: #df80ff;
            box-shadow: 0 0 20px #df80ff;
            transform: translateY(-2px);
        }
        .footer {
            margin-top: auto;
            padding: 30px 20px;
            text-align: center;
            font-size: 1rem;
            color: #8c8c8c;
            line-height: 1.6;
        }
        .footer span {
            color: #cc00ff;
            font-weight: bold;
            text-shadow: 0 0 5px rgba(204, 0, 255, 0.5);
        }
    </style>
</head>
<body>

    <h1>SCTP PROXY</h1>
    
    <div class="container">
        <div class="info-box">
            <h3><svg style="vertical-align: middle; margin-right: 8px; filter: drop-shadow(0 0 4px #cc00ff);" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#cc00ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line><line x1="10" y1="6" x2="14" y2="6"></line><line x1="10" y1="18" x2="14" y2="18"></line></svg> Proxy IP Address</h3>
            <div id="proxy-ip-display" class="proxy-ip">172.22.0.43</div>
        </div>
        
        <div class="info-box">
            <h3>📡 Upstream Traffic Target IPs (AMFs)</h3>
            <ul id="amf-list">
                <li>Loading dynamically...</li>
            </ul>
        </div>

        <div class="info-box">
            <h3>➕ Add Target IP Manually</h3>
            <div class="input-group">
                <input type="text" id="new-amf-ip" placeholder="e.g., 172.22.0.11">
                <button onclick="addAmf()">Add IP</button>
            </div>
            <div id="status-message" style="font-size: 0.9rem; margin-top: 15px; min-height: 20px;"></div>
        </div>
    </div>

    <div class="footer">
        This proxy is designed by <span>Eng:Ahmed Youssef</span> to work with <span>open5Gs</span> project
    </div>

    <script>
        async function fetchAmfs() {
            try {
                const response = await fetch('/amfs');
                const data = await response.json();
                const list = document.getElementById('amf-list');
                
                let htmlContent = '';
                
                if(data.upstream_amfs.length === 0) {
                    htmlContent = '<li>No IPs configured</li>';
                } else {
                    data.upstream_amfs.forEach(amf => {
                        htmlContent += `<li>${amf.ip}:${amf.port}</li>`;
                    });
                }
                
                // Only update DOM if changes happened to prevent flickering
                if(list.innerHTML !== htmlContent) {
                    list.innerHTML = htmlContent;
                }
                
            } catch (error) {
                console.error('Error fetching AMFs:', error);
            }
        }

        async function addAmf() {
            const ipInput = document.getElementById('new-amf-ip').value.trim();
            const statusMsg = document.getElementById('status-message');
            
            if(!ipInput) {
                statusMsg.innerText = 'Please enter a valid IP Address.';
                statusMsg.style.color = '#ff4d4d'; // Soft red
                return;
            }

            try {
                const response = await fetch('/amfs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ip: ipInput, port: 38412, weight: 1 })
                });

                const result = await response.json();

                if(response.ok) {
                    statusMsg.innerText = `[SUCCESS] IP ${ipInput} added successfully!`;
                    statusMsg.style.color = '#cc00ff';
                    document.getElementById('new-amf-ip').value = '';
                    fetchAmfs(); // Refresh the list right away
                } else {
                    statusMsg.innerText = `[ERROR] ${result.detail || 'Failed to add IP.'}`;
                    statusMsg.style.color = '#ff4d4d';
                }
            } catch (error) {
                statusMsg.innerText = '[ERROR] Network connection failed.';
                statusMsg.style.color = '#ff4d4d';
            }
            
            // Auto clear message after 4 seconds
            setTimeout(() => { statusMsg.innerText = ''; }, 4000);
        }

        // Fetch on load, then auto-refresh every 2.5 seconds
        fetchAmfs();
        setInterval(fetchAmfs, 2500);
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def serve_web_ui():
    """
    يعرض واجهة الويب التفاعلية.
    """
    return WEB_UI_HTML


@app.get("/amfs")
def get_amfs():
    return {
        "upstream_amfs": upstream_amfs,
        "active_sessions_count": len(active_sessions)
    }


@app.post("/amfs")
def add_amf(amf: AMFNode):
    for existing_amf in upstream_amfs:
        if existing_amf.ip == amf.ip and existing_amf.port == amf.port:
            raise HTTPException(
                status_code=400,
                detail="AMF IP already exists."
            )

    upstream_amfs.append(amf)

    logger.info(
        f"Scale-out: Added new AMF {amf.ip}:{amf.port}"
    )

    return {
        "message": "AMF added successfully",
        "upstream_amfs": upstream_amfs
    }


@app.delete("/amfs/{amf_ip}")
def remove_amf(amf_ip: str):
    global upstream_amfs

    old_count = len(upstream_amfs)

    upstream_amfs = [
        amf for amf in upstream_amfs
        if amf.ip != amf_ip
    ]

    if len(upstream_amfs) == old_count:
        raise HTTPException(
            status_code=404,
            detail="AMF IP not found."
        )

    sessions_to_remove = [
        key for key, value in active_sessions.items()
        if value[0] == amf_ip
    ]

    for key in sessions_to_remove:
        del active_sessions[key]

    logger.info(f"Scale-in: Removed AMF {amf_ip}")

    return {
        "message": "AMF removed successfully",
        "upstream_amfs": upstream_amfs
    }


async def main():
    proxy_task = asyncio.create_task(
        proxy_instance.start_proxy()
    )

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )

    api_server = uvicorn.Server(config)

    await asyncio.gather(
        proxy_task,
        api_server.serve()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("SCTP_PROXY stopped.")