# ash4d.com — Private AI Platform & Kubernetes Infrastructure

A production-grade private AI platform running on a single-node k3s cluster, featuring local LLM inference (Qwen3 27B on a Tesla V100), RAG over a Milvus vector database, agentic MCP tooling, GPU-accelerated image generation, and full observability with Prometheus/Grafana. The public site is served from Cloudflare Workers static assets, deployed by GitHub Actions; a Cloudflare Tunnel exposes the lab-hosted services that need a public hostname.

This repository documents the architecture, design decisions, and deployment patterns behind the platform.

## Architecture Overview

```mermaid
graph TB
    subgraph Internet
        USER((Users))
        GH[GitHub]
    end

    subgraph CF["Cloudflare"]
        WORKER[Workers Static Assets<br/>ash4d.com + www]
        CFTUNNEL{{Cloudflare Tunnel}}
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

    USER -->|HTTPS| WORKER
    USER -->|HTTPS| CFTUNNEL
    USER -->|LAN| OWUI
    GH -->|Actions + wrangler| WORKER
    GH -->|GitOps| FLEET
    CFTUNNEL --- TRAEFIK

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
    classDef cf fill:#f6821f,stroke:#d96b0f,color:#fff

    class V100,GTX gpu
    class OLLAMA,OWUI,MILVUS,MCPO,K8S_MCP,GH_MCP,INDEXER,ATTU,COMFY ai
    class FLEET,LONGHORN,METALLB,TRAEFIK,CERTMGR,NVIDIA infra
    class PROM,GRAFANA,ALERT mon
    class NODERED,EMBY,RESILIO,HERMES app
    class WORKER,CFTUNNEL cf
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

## Public Serving and GitOps

### The site — Cloudflare Workers

```mermaid
flowchart TD
    subgraph GitHub
        REPO[("darthzen/ash4d.com<br/>site/ assets")]
        GHA[GitHub Actions<br/>wrangler deploy]
    end

    subgraph Cloudflare
        WORKER[Workers Static Assets]
        DNS[("ash4d.com + www<br/>custom domains")]
        RR[Redirect Rule<br/>www → apex 301]
    end

    USER((Users))

    REPO -->|push to main, site/**| GHA
    GHA -->|deploy| WORKER
    DNS --> WORKER
    USER --> RR
    RR --> WORKER
    USER --> WORKER

    classDef cf fill:#f6821f,stroke:#d96b0f,color:#fff
    class WORKER,DNS,RR cf
```

The public site has no home-lab dependency in its serving path. Assets live in
`site/`, a push to `main` touching them triggers `wrangler deploy`, and
Cloudflare serves them from its edge. Apex and `www` are Worker custom domains;
a zone Redirect Rule 301s `www` to the apex so one canonical hostname answers.

`html_handling` is set to `none` so `/fossa-mcp.html` keeps serving at its own
URL. The ten-line `src/index.js` exists only to map `/` to `/index.html`, which
that setting otherwise leaves unresolved.

The site previously ran as an nginx pod on the home cluster, fronted by a k3s
caching proxy on GCP and reached across a Tailscale mesh. That path was retired
in August 2026 — see `docs/cloudflare-migration.md` for the runbook and the
reasoning.

### SUSE Fleet — GitOps for the cluster

```mermaid
flowchart TD
    subgraph GitHub
        LABREPO[("darthzen/lab-fleet<br/>bundle manifests")]
    end

    subgraph Rancher["Rancher / Fleet Controller"]
        GITREPO[GitRepo CR]
        BUNDLE[Bundle]
    end

    subgraph HomeLab["Home Lab k3s"]
        AGENT[Fleet Agent]
        WORKLOADS[Namespaced workloads]
    end

    LABREPO -->|poll 60s| GITREPO
    GITREPO --> BUNDLE
    BUNDLE -->|deploy| AGENT
    AGENT -->|apply| WORKLOADS

    classDef fleet fill:#059669,stroke:#047857,color:#fff
    class GITREPO,BUNDLE,AGENT fleet
```

**SUSE Fleet** manages the cluster's own workloads from `darthzen/lab-fleet`.
Bundles run with `correctDrift` enabled, so an out-of-band `kubectl edit` is
reverted on the next reconcile — the repo is the source of truth, and changing
the cluster means changing the repo.

### Cloudflare Tunnel

Lab-hosted services that need a public hostname reach the internet through a
Cloudflare Tunnel rather than an inbound firewall hole. `buzz.ash4d.com` routes
through Traefik; `ollama.ash4d.com` goes straight to its LoadBalancer VIP and
sits behind Cloudflare Access. Everything else the tunnel receives gets a 404.

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
│   └── cloudflare-migration.md  # Workers migration runbook
├── lab/                         # git submodule → lab-fleet: cluster configs
│   ├── 00-host/ … 14-cluster-mgmt/  (install-ordered, one dir per component)
│   └── README.md                # recreation runbook
├── site/                        # the site itself, baked into the image
│   ├── index.html
│   ├── style.css
│   └── img/
│   └── _headers                 # security headers + cache policy
├── src/
│   └── index.js                 # maps / to /index.html
├── wrangler.jsonc               # Worker config, custom domains
└── .github/workflows/
    └── deploy-worker.yaml       # push to main + site/** -> wrangler deploy
```

## Related Repositories

- [`ollama-code-mcp`](https://github.com/darthzen/ollama-code-mcp) — MCP server that delegates coding tasks from Claude Code to local Ollama/Qwen3, with file-aware tools and batch refactoring
- [`lab-fleet`](https://github.com/darthzen/lab-fleet) — Complete home-lab cluster configuration (helm values, manifests, recovered service sources), mounted here as the `lab/` submodule and curated from live cluster state
- [`k8s-demos`](https://github.com/darthzen/k8s-demos) — Kubernetes demonstration materials including NeuVector container security

## Tech Stack Summary

| Layer | Technology |
|---|---|
| **OS** | openSUSE Leap 16.0 |
| **Orchestration** | k3s v1.35 |
| **GitOps** | SUSE Fleet (cluster), GitHub Actions + wrangler (site) |
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
