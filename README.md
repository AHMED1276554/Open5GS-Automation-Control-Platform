# Open5GS Automation Control Platform

A comprehensive automation platform designed to manage and control an Open5GS 5G core network end-to-end (E2E). This platform simplifies the deployment, monitoring, and scaling of 5G network components through an intuitive web interface.

## 📦 Getting Started
The project is currently stable and hosted on the **`master` branch**. You can clone the repository and run the application immediately with the provided configuration.

## 🚀 Key Features

*   **One-Click Deployment**: Easily launch or terminate core components, gNBs, and UEs.
*   **Scalability & Customization**: 
    *   Launch any number of gNBs and UEs.
    *   Define specific connectivity (UE-to-gNB mapping).
    *   Customize **TAC** (Tracking Area Code) per gNB and **SST** (Slice/Service Type) per UE.
*   **Interactive Visual Canvas**: 
    *   Visualize network topology with draggable, zoomable nodes.
    *   Real-time representation of component connectivity.
*   **Monitoring & Diagnostics**:
    *   **Live Logs**: View individual component logs or centralized system-wide logs.
    *   **Connectivity Checks**: Built-in Ping testing to verify UE-to-Core data plane connectivity and IP assignment.
    *   **Health Checks**: AI-driven monitoring for container health.
*   **AI Agent Assistant**: 
    *   Interactive chat interface to deploy network components via natural language.
    *   Automated log analysis and error troubleshooting.
    *   Automated health check reports.
*   **Infrastructure Management**:
    *   **Load Balancing**: Automatic AMF scaling (1 AMF per 5 UEs).
    *   **Custom SCTP Proxy**: Efficiently distributes traffic from gNBs and UEs across multiple AMFs.
*   **Integrated Monitoring Stack**:
    *   **Prometheus & Grafana**: Track real-time network KPIs.
    *   **Database Management**: Built-in redirect to web-based subscriber data management.

## 🛠 Side Panel & Navigation
*   **AI Agent**: Troubleshoot, deploy, and generate network reports.
*   **System Logs**: View centralized logs or click any canvas node for specific component logs.
*   **Network Topology**: List of active components and their status.
*   **Architecture**: View the underlying Open5GS network architecture.
*   **Subscriber DB**: Manage network subscribers.
*   **Monitoring**: Quick links to Prometheus and Grafana dashboards.
*   **GitHub**: Quick access to the project repository.

## 📋 Prerequisites
*   Python 3.x
*   Flask framework
*   Docker & Kubernetes/Minikube
*   Open5GS components

## 🚀 How to Run

1.  **Clone the Repository** (the stable version is in the `master` branch):
    ```bash
    git clone https://github.com/AHMED1276554/Open5GS-Automation-Control-Platform.git
    cd Open5GS-Automation-Control-Platform
    ```
2.  **Launch** the application:
    ```bash
    sudo python3 app.py
    ```
3.  **Access** the dashboard via your browser:
    *   `http://localhost:5000`

---
*Built with ❤️ for 5G Research and Network Automation.*
