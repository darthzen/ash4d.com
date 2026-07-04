# AI/ML Platform — Deep Dive

This document covers the AI platform running in the `ai` namespace of the home lab k3s cluster.

## Component Inventory

| Component | Type | Replicas | Storage | LAN IP | Purpose |
|---|---|---|---|---|---|
| Ollama | Deployment | 1 | 100 Gi (models) | 192.168.7.153 | LLM inference |
| Open WebUI | StatefulSet | 1 | 10 Gi | 192.168.7.151 | Chat interface |
| Open WebUI Pipelines | Deployment | 1 | 2 Gi | — | Function execution |
| Open WebUI Redis | Deployment | 1 | — | — | Session cache |
| Milvus Standalone | Deployment | 1 | 50 Gi (vectors) | — | Vector database |
| Milvus etcd | StatefulSet | 1 | 10 Gi | — | Milvus metadata |
| Milvus MinIO | Deployment | 1 | 50 Gi | — | Object storage |
| Attu | Deployment | 1 | — | 192.168.7.152 | Milvus GUI |
| k8s-mcp-server | Deployment | 1 | — | — | K8s introspection MCP |
| github-mcp-server | Deployment | 1 | — | — | GitHub access MCP |
| mcpo | Deployment | 1 | — | — | MCP→OpenAPI gateway |
| k8s-docs-indexer | CronJob | — | 1 Gi (state) | — | Weekly RAG indexing |
| ComfyUI | Deployment | 0/1 | 150 Gi | 192.168.7.154 | Image generation |
| ComfyUI FileBrowser | Deployment | 0/1 | — | 192.168.7.155 | Model/output browser |

## LLM Inference — Ollama + Qwen3

### Model Configuration

- **Model:** `qwen3:32b` (Qwen3 27B parameters, 32-bit quantization label)
- **GPU:** Tesla V100 32GB — pinned via NVIDIA Device Plugin resource requests
- **Storage:** 100 Gi Longhorn PVC for model weights and cache
- **API:** Exposed on LAN at `192.168.7.153:11434` via MetalLB LoadBalancer
- **In-cluster DNS:** `ollama.ai.svc.cluster.local:11434`

### Extended Thinking (Qwen3 Think Mode)

Qwen3 supports toggling extended chain-of-thought reasoning via `/think` and `/no_think` prompt suffixes. The [`ollama-code-mcp`](https://github.com/darthzen/ollama-code-mcp) server leverages this: coding tasks that benefit from deeper reasoning (review, refactor, fix, test generation) default to `think=True`, while fast-turnaround tasks (explain) default to `think=False`.

### Integration Points

1. **Open WebUI** — Primary user-facing chat interface. Connects to Ollama for inference and to Milvus for RAG-augmented responses.
2. **Claude Code (Mac)** — Uses `ollama-code-mcp` to offload mechanical coding tasks (boilerplate, test writing, diff review, batch refactors) to the local Qwen3 instance, saving cloud context window and tokens.
3. **Pipelines** — Open WebUI's function execution framework, enabling custom processing workflows.

## RAG Pipeline — Milvus + Indexer

### Indexing Architecture

The `k8s-docs-indexer` CronJob runs weekly (Sundays at 3:00 AM) and performs incremental document indexing:

1. Scans configured document sources for new or updated content
2. Chunks documents into embedding-sized segments
3. Generates vector embeddings using `nomic-embed-text` via the Ollama API
4. Upserts vectors into Milvus collections
5. Persists indexing state (checksums, timestamps) in a 1 Gi PVC to avoid re-processing unchanged documents

### Milvus Architecture

Milvus runs in **standalone mode** (single-process, suitable for <1M vectors) with two backing stores:

- **etcd** (StatefulSet, 10 Gi) — Collection metadata, schema definitions, segment info
- **MinIO** (Deployment, 50 Gi) — Segment binlog files and index files as S3-compatible object storage

The standalone process itself uses a 50 Gi volume for the vector index data.

**Attu** provides a web GUI at `192.168.7.152` for:
- Browsing collections and their schemas
- Running similarity searches interactively
- Inspecting vector distributions and index health
- Testing embedding quality before production queries

### Query Path

When a user asks a question in Open WebUI that matches a RAG-enabled collection:

1. Open WebUI embeds the query using the same `nomic-embed-text` model
2. Performs a similarity search against Milvus
3. Retrieves the top-k most relevant document chunks
4. Constructs an augmented prompt with the retrieved context
5. Sends the augmented prompt to Ollama/Qwen3 for generation

## MCP — Model Context Protocol Tooling

### Architecture

Three MCP servers run as Kubernetes Deployments, each exposing a focused set of tools:

#### k8s-mcp-server
- **Purpose:** Read-only Kubernetes cluster introspection
- **Tools:** List/describe pods, deployments, services, namespaces; read pod logs; get resource utilization
- **Security:** Service account with RBAC limited to `get`, `list`, `watch` verbs — no create/update/delete
- **Transport:** Streamable HTTP on port 8080

#### github-mcp-server
- **Purpose:** GitHub repository browsing and code search
- **Tools:** List repos, read file contents, search code, browse issues and PRs
- **Auth:** GitHub personal access token (read-only scoped)
- **Transport:** Streamable HTTP on port 8080

#### systemd-mcp (host-level)
- **Purpose:** Host service inspection
- **Tools:** List units, check service status, view journal logs
- **Security:** Read-only — no start/stop/restart capabilities exposed

### mcpo Gateway

**mcpo** bridges the MCP protocol into OpenAPI, making MCP tools accessible to Open WebUI (which speaks OpenAPI/function-calling, not MCP natively). This means Qwen3 can invoke Kubernetes queries, browse GitHub repos, and inspect host services as tool calls during a conversation.

The gateway runs as a single Deployment with ClusterIP service, aggregating all downstream MCP servers into a unified OpenAPI surface.

### Claude Code Integration

Claude Code on Rick's Mac connects to MCP servers directly — `k8s-mcp-server` and `github-mcp-server` over the LAN, and `ollama-code-mcp` for delegating coding tasks to the local Qwen3 instance. This creates a hybrid workflow:

- **Architecture-sensitive work** stays in Claude (cloud) for maximum reasoning quality
- **Mechanical tasks** (test generation, boilerplate, diff review) route to the local Ollama instance via MCP, saving context window and API tokens
- **Cluster introspection** happens through the k8s MCP server — Claude can inspect pod status, read logs, and understand the running system without SSH access

## Image Generation — ComfyUI

ComfyUI provides a node-based Stable Diffusion workflow editor, pinned to the GTX 1070 8GB to avoid GPU contention with Ollama on the V100.

- **Storage:** 150 Gi Longhorn PVC (the largest volume in the cluster) for diffusion models, LoRAs, and generated outputs
- **FileBrowser:** A companion deployment provides web-based file management for models and outputs
- **Scaling:** Currently scaled to 0 replicas when not in active use, freeing the GTX 1070 for other workloads

## Namespace Resource Summary

```
Total Persistent Storage: ~423 Gi
  Ollama models:     100 Gi
  ComfyUI data:      150 Gi
  Milvus vectors:     50 Gi
  Milvus objects:     50 Gi
  GLM models:         50 Gi
  Milvus metadata:    10 Gi
  Open WebUI:         10 Gi
  Pipelines:           2 Gi
  Indexer state:       1 Gi

Running Pods: 14 (+ completed jobs)
Services: 15 (4 LoadBalancer, 11 ClusterIP)
```
