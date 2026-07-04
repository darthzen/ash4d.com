# ash4d.com — Private AI Platform & Multi-Cluster Kubernetes Infrastructure

A production-grade private AI platform running on a single-node k3s cluster, featuring local LLM inference (Qwen3 27B on a Tesla V100), RAG over a Milvus vector database, agentic MCP tooling, GPU-accelerated image generation, and full observability with Prometheus/Grafana. A second k3s cluster on GCP serves the public-facing site, managed as a downstream cluster via SUSE Fleet over a Tailscale mesh.

This repository documents the architecture, design decisions, and deployment patterns behind the platform.

## Architecture Overview

```mermaid
graph TB
    subgraph Internet
        USER((Users))
        GH[GitHub]
    end

    subgraph GCP["GCP — us-central1 (e2-small)"]
        subgraph gcp_k3s["k3s (downstream)"]
            NGINX[nginx — ash4d.com]
            CM2[cert-manager]
        end
    end

    subgraph Tailscale["Tailscale Mesh"]
        TS_TUNNEL{{encrypted tunnel}}
    end

    subgraph HomeLab["Home Lab — openSUSE Leap 16.0"]
        subgraph k3s["k3s v1.35 — 16 vCPU / 64 GB / 2× NVIDIA GPU"]

            subgraph ai_ns["AI Platform (namespace: ai)"]
                OLLAMA[Ollama<br/>Qwen3 27B]
                OWUI[Open WebUI<br/>+ Pipelines + Redis]
                MILVUS[Milvus Standalone<br/>+ etcd + MinIO]
                MCPO[mcpo<br/>OpenAPI Gateway]
                K8S_MCP[k8s-mcp-server]
                GH_MCP[github-mcp-server]
                INDEXER[k8s-docs-indexer<br/>Weekly CronJob]
                ATTU[Attu — Milvus GUI]
                COMFY[ComfyUI<br/>Image Generation]
            end

            subgraph infra["Infrastructure"]
                FLEET[Fleet Controller<br/>+ Agent]
                LONGHORN[Longhorn<br/>Distributed Storage]
                METALLB[MetalLB<br/>Load Balancer]
                TRAEFIK[Traefik<br/>Ingress]
                CERTMGR[cert-manager]
                NVIDIA[NVIDIA Device Plugin<br/>+ DCGM Exporter]
            end

            subgraph monitoring["Observability"]
                PROM[Prometheus]
                GRAFANA[Grafana]
                ALERT[Alertmanager]
            end

            subgraph apps["Applications"]
                NODERED[Node-RED<br/>Automation]
                EMBY[Emby<br/>Media Server]
                RESILIO[Resilio Sync]
                HERMES[Hermes Agent]
            end
        end

        V100[Tesla V100 32GB]
        GTX[GTX 1070 8GB]
    end

    USER -->|HTTPS| NGINX
    USER -->|LAN| OWUI
    GH -->|GitOps| FLEET
    FLEET -->|Bundle deploy| gcp_k3s
    TS_TUNNEL --- gcp_k3s
    TS_TUNNEL --- k3s

    OLLAMA ---|GPU pinned| V100
    COMFY ---|GPU pinned| GTX
    OWUI --> OLLAMA
    OWUI --> MCPO
    MCPO --> K8S_MCP
    MCPO --> GH_MCP
    INDEXER --> MILVUS
    OWUI --> MILVUS
    NVIDIA ---|scheduling| V100
    NVIDIA ---|scheduling| GTX

    classDef gpu fill:#f59e0b,stroke:#d97706,color:#000
    classDef ai fill:#3b82f6,stroke:#2563eb,color:#fff
    classDef infra fill:#6b7280,stroke:#4b5563,color:#fff
    classDef mon fill:#10b981,stroke:#059669,color:#fff
    classDef app fill:#8b5cf6,stroke:#7c3aed,color:#fff
    classDef gcp fill:#4285f4,stroke:#1a73e8,color:#fff

    class V100,GTX gpu
    class OLLAMA,OWUI,MILVUS,MCPO,K8S_MCP,GH_MCP,INDEXER,ATTU,COMFY ai
    class FLEET,LONGHORN,METALLB,TRAEFIK,CERTMGR,NVIDIA infra
    class PROM,GRAFANA,ALERT mon
    class NODERED,EMBY,RESILIO,HERMES app
    class NGINX,CM2 gcp
```

## Platform Specifications

| Resource | Details |
|---|---|
| **Node** | Single-node k3s (`sdf1`) on openSUSE Leap 16.0 |
| **Kernel** | 6.12.0-160000.33-default |
| **k3s Version** | v1.35.5+k3s1 |
| **CPU** | 16 vCPU |
| **Memory** | 64 GB |
| **GPU 0** | NVIDIA Tesla V100 32GB — LLM inference (Ollama/Qwen3) |
| **GPU 1** | NVIDIA GTX 1070 8GB — Image generation (ComfyUI) |
| **Container Runtime** | containerd 2.2.3-k3s1 |
| **Persistent Storage** | ~500 Gi across Longhorn volumes |
| **Running Pods** | 60+ across 12 namespaces |

## AI/ML Platform

The AI platform runs entirely in the `ai` namespace and provides three core capabilities: local LLM inference, retrieval-augmented generation (RAG), and agentic tool use via the Model Context Protocol (MCP).

### LLM Inference

```mermaid
flowchart LR
    subgraph Client
        OWUI[Open WebUI]
        CC[Claude Code]
    end

    subgraph Inference["Ollama (V100 32GB)"]
        QWEN[Qwen3 27B<br/>qwen3:32b]
    end

    subgraph Storage
        PVC[100 Gi Longhorn PVC<br/>Model Weights]
    end

    OWUI -->|HTTP :11434| QWEN
    CC -->|MCP → Ollama| QWEN
    QWEN --- PVC

    classDef inference fill:#f59e0b,stroke:#d97706,color:#000
    class QWEN,PVC inference
```

**Ollama** serves Qwen3 27B (`qwen3:32b`) pinned to the Tesla V100 via NVIDIA device plugin GPU scheduling. The model weights live on a 100 Gi Longhorn persistent volume. The Ollama API is exposed on the LAN via MetalLB (`192.168.7.153:11434`), making it accessible to any device on the network.

**Open WebUI** provides the chat interface with Redis-backed sessions and a Pipelines sidecar for function execution. It connects directly to Ollama for inference and to Milvus for RAG-augmented responses.

### RAG Pipeline

```mermaid
flowchart TD
    subgraph Indexing["Weekly Indexing (CronJob — Sundays 3 AM)"]
        CRON[k8s-docs-indexer]
        EMBED[nomic-embed-text<br/>via Ollama]
    end

    subgraph VectorDB["Milvus Standalone"]
        MILVUS[(Vector Store<br/>50 Gi)]
        ETCD[(etcd<br/>Metadata)]
        MINIO[(MinIO<br/>Object Storage<br/>50 Gi)]
    end

    subgraph Query["Query Path"]
        OWUI[Open WebUI] -->|semantic search| MILVUS
        MILVUS -->|relevant chunks| OWUI
        OWUI -->|prompt + context| OLLAMA[Ollama / Qwen3]
    end

    CRON -->|embed documents| EMBED
    EMBED -->|vectors| MILVUS
    MILVUS --- ETCD
    MILVUS --- MINIO

    classDef rag fill:#8b5cf6,stroke:#7c3aed,color:#fff
    class CRON,EMBED,MILVUS,ETCD,MINIO rag
```

A weekly Kubernetes CronJob (`k8s-docs-indexer`) incrementally indexes documents, embeds them using `nomic-embed-text` via Ollama, and stores the vectors in Milvus. The indexer maintains state in a 1 Gi PVC to track what's already been processed, avoiding redundant embeddings.

Milvus runs in standalone mode backed by etcd (metadata) and MinIO (object storage), with 50 Gi Longhorn volumes for both the vector store and object storage. **Attu** provides a web GUI for collection inspection and query testing, exposed on the LAN via MetalLB.

### Agentic MCP Tooling

```mermaid
flowchart LR
    subgraph Clients
        OWUI[Open WebUI]
        CC[Claude Code<br/>on Mac]
    end

    subgraph Gateway
        MCPO[mcpo<br/>MCP → OpenAPI<br/>Bridge]
    end

    subgraph MCP_Servers["MCP Servers"]
        K8S[k8s-mcp-server<br/>Read-only cluster<br/>introspection]
        GH[github-mcp-server<br/>Repository access]
        SYS[systemd-mcp<br/>Host service mgmt]
    end

    subgraph Targets
        K8S_API[k3s API Server]
        GH_API[GitHub API]
        HOST[Host systemd]
    end

    OWUI -->|OpenAPI| MCPO
    CC -->|MCP protocol| K8S
    CC -->|MCP protocol| GH
    MCPO --> K8S
    MCPO --> GH
    MCPO --> SYS
    K8S -->|RBAC scoped| K8S_API
    GH --> GH_API
    SYS --> HOST

    classDef mcp fill:#3b82f6,stroke:#2563eb,color:#fff
    class MCPO,K8S,GH,SYS mcp
```

The platform exposes cluster and development tools as MCP servers, enabling AI agents to take actions:

- **k8s-mcp-server** — Read-only Kubernetes introspection (pods, deployments, services, logs). RBAC-scoped to prevent mutations.
- **github-mcp-server** — Repository browsing, issue/PR access, code search against Rick's GitHub repositories.
- **systemd-mcp** — Host-level service management and introspection.

**mcpo** bridges these MCP servers into Open WebUI via an OpenAPI gateway, so Qwen3 can invoke Kubernetes queries, browse GitHub repos, and inspect host services as tool calls during a conversation — without granting the model write access to anything.

Claude Code on Rick's Mac connects to the MCP servers directly over the LAN, using the [`ollama-code-mcp`](https://github.com/darthzen/ollama-code-mcp) server to offload boilerplate generation, diff review, and batch refactors to the local Qwen3 instance instead of consuming cloud tokens.

### Image Generation

**ComfyUI** is available for Stable Diffusion workflows, pinned to the GTX 1070 to avoid contention with the V100 running Ollama. It has a 150 Gi Longhorn volume for models, outputs, and workflows. Currently scaled to zero when not in use.

## Infrastructure Layer

### Storage — Longhorn

All persistent data uses **Longhorn** distributed block storage with CSI integration. Longhorn provides snapshot, backup, and volume replication capabilities. Current allocation:

| Volume | Namespace | Size | Purpose |
|---|---|---|---|
| `ollama` | ai | 100 Gi | LLM model weights |
| `comfyui-data` | ai | 150 Gi | Diffusion models + outputs |
| `milvus` | ai | 50 Gi | Vector database |
| `milvus-minio` | ai | 50 Gi | Object storage (Milvus) |
| `glm-model-pvc` | ai | 50 Gi | Additional model storage (RWX) |
| `prometheus-db` | monitoring | 50 Gi | Metrics retention |
| `data-milvus-etcd-0` | ai | 10 Gi | Vector DB metadata |
| `open-webui` | ai | 10 Gi | Chat history + config |
| `grafana` | monitoring | 10 Gi | Dashboard state |
| `hermes-data` | hermes | 10 Gi | Agent state |
| Other volumes | various | 8 Gi | Node-RED, pipelines, indexer |
| **Total** | | **~498 Gi** | |

### Networking — MetalLB + Traefik

**MetalLB** provides bare-metal LoadBalancer services, assigning LAN IPs from a configured pool:

| IP | Service | Port |
|---|---|---|
| `192.168.7.150` | Traefik (Ingress) | 80, 443 |
| `192.168.7.151` | Open WebUI | 80 |
| `192.168.7.152` | Attu (Milvus GUI) | 80 |
| `192.168.7.153` | Ollama API | 11434 |
| `192.168.7.154` | ComfyUI | 80 |
| `192.168.7.155` | ComfyUI FileBrowser | 80 |
| `192.168.7.156` | Resilio Sync | 8888, 55555 |
| `192.168.7.157` | Emby | 8096, 8920 |
| `192.168.7.158` | Node-RED | 1880 |

**Traefik** handles ingress routing; **cert-manager** manages TLS certificates.

### GPU Management

The **NVIDIA Device Plugin** exposes both GPUs as schedulable resources (`nvidia.com/gpu: 2`). GPU affinity is controlled through resource requests in pod specs — Ollama requests the V100 (by UUID), ComfyUI requests the GTX 1070.

**DCGM Exporter** (DaemonSet) scrapes GPU telemetry (utilization, temperature, memory, power draw) and exposes it as Prometheus metrics, feeding into Grafana dashboards for real-time GPU monitoring.

## Multi-Cluster Management

### SUSE Fleet — GitOps at Scale

```mermaid
flowchart TD
    subgraph GitHub
        REPO[("darthzen/ash4d.com<br/>deploy/ manifests")]
    end

    subgraph HomeLab["Home Lab k3s (Fleet Controller)"]
        FC[Fleet Controller]
        GITREPO[GitRepo CR]
        BUNDLE[Bundle]
    end

    subgraph Tailscale
        MESH{{Tailscale Mesh<br/>Encrypted P2P}}
    end

    subgraph GCP["GCP k3s (Downstream)"]
        FA[Fleet Agent]
        SITE[ash4d.com<br/>nginx + Hugo]
    end

    REPO -->|poll| GITREPO
    GITREPO --> FC
    FC -->|build| BUNDLE
    BUNDLE -->|deploy via| MESH
    MESH --> FA
    FA -->|apply| SITE

    classDef fleet fill:#059669,stroke:#047857,color:#fff
    class FC,GITREPO,BUNDLE,FA fleet
```

**SUSE Fleet** runs on the home cluster as both controller and local agent. The GCP cluster registers as a downstream cluster, with the Fleet agent connecting outbound to the controller over the Tailscale tunnel.

The deployment pipeline is fully GitOps:

1. Push site changes to `darthzen/ash4d.com` on GitHub
2. GitHub Actions builds the Hugo site and container image
3. Fleet detects the updated manifests in `deploy/`
4. Fleet bundles the changes and pushes them to the GCP downstream cluster
5. The site rolls with zero manual intervention

### Tailscale Mesh

Tailscale provides the connectivity layer between clusters — no VPN configuration, no port forwarding, no firewall holes. The GCP instance joins Rick's existing tailnet, and Fleet's agent communicates over the encrypted mesh. SSH access to the GCP instance also runs through Tailscale SSH.

## Observability

The full **Rancher Monitoring** stack provides production-grade observability:

- **Prometheus** — Metrics collection with 50 Gi retention, scraping all cluster components including GPU metrics via DCGM Exporter
- **Grafana** — Dashboards for cluster health, GPU utilization, pod resource consumption, and AI workload metrics
- **Alertmanager** — Alert routing and notification
- **kube-state-metrics** — Kubernetes object metrics
- **node-exporter** — Host-level system metrics
- **DCGM Exporter** — NVIDIA GPU telemetry (utilization, temperature, memory, power, ECC errors)

## Security Posture

- **MCP servers are read-only** — RBAC-scoped Kubernetes service accounts prevent AI agents from mutating cluster state
- **MCP tool whitelists** — Each MCP server exposes only the specific tools needed; no blanket access
- **Network segmentation** — Internal services (Milvus, etcd, MinIO, Redis) use ClusterIP with no external exposure
- **Longhorn encryption** — Storage volumes support at-rest encryption
- **Non-root containers** — Workloads run as non-root where supported
- **Tailscale ACLs** — Inter-cluster traffic is scoped to specific ports and tagged devices
- **cert-manager** — Automated TLS certificate lifecycle management
- **GPU isolation** — Dedicated GPU assignment prevents model inference from contending with image generation workloads

## Repository Structure

```
ash4d.com/
├── README.md                    # This document
├── docs/
│   ├── architecture.md          # Deep-dive architecture documentation
│   ├── ai-platform.md           # AI/ML platform details
│   ├── infrastructure.md        # Infrastructure layer documentation
│   └── deployment-plan.md       # GCP deployment plan
├── site/                        # Hugo source (future)
│   ├── config.toml
│   ├── content/
│   └── themes/
├── deploy/                      # Kubernetes manifests (Fleet-managed)
│   ├── namespace.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   └── certificate.yaml
├── Dockerfile
└── .github/workflows/
    └── build-and-push.yaml
```

## Related Repositories

- [`ollama-code-mcp`](https://github.com/darthzen/ollama-code-mcp) — MCP server that delegates coding tasks from Claude Code to local Ollama/Qwen3, with file-aware tools and batch refactoring
- [`lab-fleet`](https://github.com/darthzen/lab-fleet) — Fleet GitRepo configurations for the home lab cluster
- [`k8s-demos`](https://github.com/darthzen/k8s-demos) — Kubernetes demonstration materials including NeuVector container security

## Tech Stack Summary

| Layer | Technology |
|---|---|
| **OS** | openSUSE Leap 16.0 |
| **Orchestration** | k3s v1.35 (home), k3s (GCP) |
| **Multi-Cluster** | SUSE Fleet (GitOps) |
| **LLM Inference** | Ollama + Qwen3 27B |
| **Vector Database** | Milvus (standalone) + etcd + MinIO |
| **Embeddings** | nomic-embed-text |
| **Chat Interface** | Open WebUI + Pipelines |
| **AI Tooling** | MCP (k8s, GitHub, systemd) via mcpo gateway |
| **Image Generation** | ComfyUI (Stable Diffusion) |
| **Automation** | Node-RED |
| **Storage** | Longhorn (CSI, ~500 Gi) |
| **Load Balancer** | MetalLB |
| **Ingress** | Traefik |
| **TLS** | cert-manager + Let's Encrypt |
| **GPU** | NVIDIA Tesla V100 32GB + GTX 1070 8GB |
| **GPU Monitoring** | DCGM Exporter |
| **Observability** | Prometheus + Grafana + Alertmanager |
| **Networking** | Tailscale mesh VPN |
| **Site Generator** | Hugo |
| **CI/CD** | GitHub Actions |

## About

Built by [Rick Ashford](https://www.linkedin.com/in/rickashford/) — Sales Engineering leader with 17 years at SUSE, specializing in Kubernetes, Linux, AI/LLM platforms, and open-source ecosystem strategy.
