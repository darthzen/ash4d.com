# ash4d.com — Implementation Plan

**Author:** Rick Ashford
**Date:** July 4, 2026
**Status:** Planning — no implementation actions taken
**Purpose:** Full-stack deployment plan for a portfolio/consulting site on GCP, managed via Fleet from the home k3s cluster over Tailscale.

---

## 1. GCP Compute — e2-small Provisioning

### Instance Specification

| Parameter | Value |
|---|---|
| Machine type | e2-small (2 vCPU, 2 GB RAM) |
| Region / Zone | us-central1-a (or -b/-c for preference) |
| Boot disk | 20 GB SSD persistent disk (pd-balanced) |
| OS | openSUSE Leap 15.6 |
| Network tier | Standard (sufficient for a static site) |

### OS Recommendation

**openSUSE Leap 15.6** is available on the GCP Marketplace as a community image. This is the right pick for several reasons: it's the distribution Rick knows best, zypper's memory footprint is comparable to apt, and it shares the SLE kernel/userspace that k3s is tested against. If the Marketplace image is unavailable or stale at provisioning time, the fallback order is:

1. Import a current openSUSE Leap 15.6 cloud image from `get.opensuse.org` as a custom image
2. openSUSE Tumbleweed (rolling; heavier maintenance burden)
3. SUSE Linux Enterprise Server 15 SP6 (requires registration; free dev tier exists but adds friction)
4. Debian 12 or Ubuntu 24.04 LTS as a last resort — both are well-supported by k3s and cert-manager

### Firewall Rules

Create a dedicated VPC firewall rule target tag (e.g., `ash4d-web`) applied to the instance:

| Rule name | Direction | Protocol/Port | Source | Purpose |
|---|---|---|---|---|
| `allow-http` | Ingress | TCP/80 | 0.0.0.0/0 | HTTP (redirect to HTTPS) |
| `allow-https` | Ingress | TCP/443 | 0.0.0.0/0 | HTTPS |
| `allow-ssh` | Ingress | TCP/22 | Rick's home IP + Tailscale CIDR (100.64.0.0/10) | SSH access |
| `allow-icmp` | Ingress | ICMP | 0.0.0.0/0 | Ping/diagnostics |

**Note:** Do not open NodePort ranges (30000-32767) to the public internet. Ingress traffic hits 80/443 only; the nginx Ingress controller inside k3s handles routing. SSH should be locked to known sources — Tailscale provides a stable identity regardless of IP changes, so the Tailscale CIDR cover is the primary access path.

### Static IP Allocation

Reserve an external static IP in us-central1 before creating the instance:

```
gcloud compute addresses create ash4d-ip --region=us-central1
```

Attach it to the instance's network interface at creation time. This IP is what the DNS A record will point at.

**Cost:** Static IPs attached to a running instance are free. An unused reserved IP costs $0.01/hr (~$7.30/month) — don't reserve it until the instance is ready.

### DNS — Dyn.com Configuration

In the Dyn.com Managed DNS console for the `ash4d.com` zone:

| Record | Name | Value | TTL |
|---|---|---|---|
| A | `@` (root) | `<GCP static IP>` | 300 (lower during cutover, raise to 3600 after verification) |
| A | `www` | `<GCP static IP>` | 300 → 3600 |
| CAA | `@` | `0 issue "letsencrypt.org"` | 3600 |

The CAA record is optional but recommended — it tells other CAs not to issue certs for ash4d.com, reducing the risk of unauthorized certificate issuance.

**Propagation:** After updating, verify with `dig ash4d.com @8.8.8.8` from the GCP instance. Dyn typically propagates within 1-2 minutes; the low initial TTL ensures clients pick up the change quickly.

---

## 2. k3s Installation — Memory-Optimized

### Installation Command

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
  --disable traefik \
  --disable servicelb \
  --disable metrics-server \
  --disable local-storage \
  --flannel-backend=host-gw \
  --kubelet-arg=eviction-hard=memory.available<100Mi \
  --kubelet-arg=eviction-soft=memory.available<200Mi \
  --kubelet-arg=eviction-soft-grace-period=memory.available=30s \
  --kubelet-arg=system-reserved=memory=256Mi \
  --kubelet-arg=kube-reserved=memory=128Mi \
  --kube-controller-manager-arg=node-monitor-period=60s \
  --kube-controller-manager-arg=node-monitor-grace-period=180s \
  --kube-apiserver-arg=default-watch-cache-size=50 \
  --kube-apiserver-arg=watch-cache-sizes='' \
  --etcd-arg=quota-backend-bytes=536870912" sh -
```

### What Each Flag Does

**Disabled components:**

- `--disable traefik` — Replaced by a lightweight nginx Ingress controller (or the site pod can terminate TLS directly). Traefik's CRDs and admission webhooks consume ~80-100 MB that we don't need.
- `--disable servicelb` — Klipper LB is unnecessary; the nginx pod will use `hostPort: 80` and `hostPort: 443` directly.
- `--disable metrics-server` — No HPA or `kubectl top` needed on a single-pod site. Saves ~30 MB.
- `--disable local-storage` — Site content is baked into the container image; no PVCs needed. Removes the local-path-provisioner pod.

**Flannel backend:** `host-gw` avoids VXLAN encapsulation overhead. On a single-node cluster this is academic, but it's one less thing consuming CPU cycles.

**Kubelet tuning:** The eviction thresholds are aggressive — the kubelet will start evicting pods if available memory drops below 200 MB (soft) or 100 MB (hard). The reserved memory carve-outs (256 MB system + 128 MB kube) ensure the OS and k3s processes aren't starved even under pod pressure.

**Controller-manager:** Relaxed node monitoring intervals reduce API server chatter on a single-node cluster where node health checks are largely meaningless.

**API server:** Reduced watch cache sizes to lower memory consumption. Defaults are tuned for large clusters; a single-node site server doesn't need them.

**Embedded etcd:** 512 MB quota is more than sufficient for a cluster that will hold a handful of resources. Default is 2 GB.

### Expected Memory Footprint

| Component | Estimated RSS |
|---|---|
| k3s server (API server + controller-manager + scheduler + embedded etcd) | ~350 MB |
| k3s agent (kubelet + containerd + flannel) | ~150 MB |
| CoreDNS | ~25 MB |
| cert-manager controller + webhook + cainjector | ~80 MB |
| nginx Ingress controller | ~40 MB |
| Site pod (nginx serving static files) | ~15 MB |
| **Total k8s workload** | **~660 MB** |
| openSUSE base OS (systemd, sshd, zypper cache, Tailscale) | ~250 MB |
| **Total estimated** | **~910 MB** |
| **Headroom** | **~1.1 GB** |

This leaves comfortable room for builds, zypper updates, and transient spikes. If memory becomes tight in practice, cert-manager's cainjector is the first candidate to remove (it's only needed when cert-manager injects CA bundles into webhook configurations — a one-time operation that can be done manually).

### TLS / cert-manager / Let's Encrypt

Install cert-manager via its static manifest (not Helm — avoids Tiller overhead and Helm binary on disk):

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.0/cert-manager.yaml
```

Then create a `ClusterIssuer` for Let's Encrypt production using HTTP-01 validation:

- **Why HTTP-01 over DNS-01:** Dyn.com's DNS API is proprietary and cert-manager's Dyn solver support is limited. HTTP-01 is simpler — cert-manager creates a temporary Ingress to serve the ACME challenge on port 80, Let's Encrypt validates, done. This does require port 80 to be open, which it is per the firewall rules above.

- **Certificate resource:** A single `Certificate` CR covering `ash4d.com` and `www.ash4d.com`, stored in a Secret that the Ingress references.

- **Renewal:** cert-manager handles renewal automatically 30 days before expiry. No cron jobs needed.

---

## 3. Tailscale VPN

### Purpose

Tailscale provides the connectivity layer between Rick's home k3s cluster (running Fleet) and the GCP instance. Fleet's agent on the GCP cluster communicates back to Fleet's controller on the home cluster over the Tailscale mesh — no public Fleet API endpoint needed, no port forwarding, no firewall holes beyond what's already open for the site itself.

### Installation

```bash
# openSUSE Leap
zypper addrepo https://pkgs.tailscale.com/stable/opensuse/leap/15.6/tailscale.repo
zypper refresh
zypper install tailscale
systemctl enable --now tailscaled
tailscale up --ssh
```

The `--ssh` flag enables Tailscale SSH, which means Rick can SSH to the GCP instance via its Tailscale hostname (e.g., `ash4d`) without managing authorized_keys — Tailscale handles authentication via the tailnet identity. This is a secondary access path alongside the GCP SSH key.

### Tailnet Considerations

- The GCP instance joins Rick's existing tailnet alongside the home k3s node(s).
- Assign a meaningful hostname: `tailscale set --hostname=ash4d`
- MagicDNS resolves `ash4d` from any device on the tailnet.
- No subnet routing needed — Fleet's agent initiates the connection outbound from the GCP cluster to Fleet's controller on the home cluster, so the Tailscale point-to-point mesh is sufficient.

### Connectivity Verification

After joining the tailnet, verify bidirectional reachability:

```bash
# From GCP instance
tailscale ping <home-k3s-tailscale-ip>

# From home k3s node
tailscale ping ash4d
```

Fleet's downstream registration (Section 4) depends on this connectivity being established first.

---

## 4. Fleet Registration

### Architecture

```
Home k3s (Fleet controller)  ←—Tailscale—→  GCP k3s (downstream cluster)
       │                                              │
       ├── Fleet manager                              ├── Fleet agent
       ├── GitRepo CR → watches GitHub                ├── Receives Bundles
       └── BundleDeployment                           └── Deploys site workloads
```

Fleet's controller runs on the home cluster. The GCP cluster is registered as a downstream (managed) cluster. Fleet's agent on the downstream cluster connects outbound to the controller over the Tailscale tunnel — no inbound ports needed on the home network.

### Registration Steps

1. **Create a cluster registration token** on the home cluster:
   ```bash
   kubectl apply -f - <<EOF
   apiVersion: fleet.cattle.io/v1alpha1
   kind: ClusterRegistrationToken
   metadata:
     name: ash4d-gcp
     namespace: fleet-default
   spec: {}
   EOF
   ```

2. **Extract the registration values** — Fleet generates a registration URL and a cluster-specific token. These feed into the agent Helm chart installed on the GCP cluster.

3. **Install the Fleet agent on the GCP cluster:**
   ```bash
   helm install fleet-agent fleet/fleet-agent \
     --namespace cattle-fleet-system \
     --create-namespace \
     --set apiServerURL=https://<home-k3s-tailscale-ip>:6443 \
     --set token=<registration-token> \
     --set clusterLabels.env=prod \
     --set clusterLabels.site=ash4d
   ```

   The `apiServerURL` uses the Tailscale IP of the home k3s node — this is the critical piece that makes Fleet-over-Tailscale work. The GCP agent reaches the home Fleet controller via the Tailscale mesh without any public exposure.

4. **Verify registration** on the home cluster:
   ```bash
   kubectl get clusters.fleet.cattle.io -n fleet-default
   ```

### GitRepo Configuration

On the home cluster, create a `GitRepo` resource that watches the ash4d.com GitHub repository:

```yaml
apiVersion: fleet.cattle.io/v1alpha1
kind: GitRepo
metadata:
  name: ash4d-site
  namespace: fleet-default
spec:
  repo: https://github.com/rickashford/ash4d.com
  branch: main
  paths:
    - deploy/
  targets:
    - clusterSelector:
        matchLabels:
          site: ash4d
```

The `deploy/` directory in the repo contains the Kubernetes manifests (Deployment, Service, Ingress, Certificate). Fleet watches the repo, detects changes, builds Bundles, and pushes them to matching downstream clusters.

### What Goes in `deploy/`

- `namespace.yaml` — dedicated namespace (`ash4d`)
- `deployment.yaml` — nginx pod serving the Hugo-built static site (image from GitHub Container Registry or Docker Hub)
- `service.yaml` — ClusterIP service (no LoadBalancer needed; using hostPort)
- `ingress.yaml` — Ingress resource for ash4d.com + www.ash4d.com with TLS
- `certificate.yaml` — cert-manager Certificate CR
- `clusterissuer.yaml` — Let's Encrypt ClusterIssuer (if not already deployed)

---

## 5. Site Design and Content

### Static Site Generator: Hugo

**Recommendation: Hugo.** Rationale:

- **Single binary, zero runtime dependencies.** No Ruby (Jekyll), no Node.js (Gatsby/Next), no Python. On a 2 GB instance, this matters — the build tool doesn't need to live on the server at all since builds happen in CI, but even locally Hugo is ~15 MB with no dependency tree.
- **Build speed.** Hugo builds a 100-page site in under 1 second. This makes the CI → deploy pipeline nearly instantaneous.
- **Go-based.** Fits the systems/infrastructure profile of the site itself. If Rick wants to extend Hugo with custom shortcodes or templates, it's Go templating — not a foreign ecosystem.
- **Mature theme ecosystem.** There are polished, minimal themes suited to portfolio/consulting sites that don't require frontend toolchain knowledge.
- **Built-in asset pipeline.** SCSS compilation, image processing, minification — all built in, no webpack/vite configuration.

**Alternatives considered and rejected:**

- *Jekyll* — Ruby dependency, slower builds, GitHub Pages lock-in assumptions that don't apply here.
- *Gatsby/Next.js* — Node.js runtime, massive node_modules, overkill for a static portfolio site.
- *Zola* — Rust-based, Hugo-like philosophy, but much smaller theme/community ecosystem. Worth revisiting if Hugo proves frustrating.

### Recommended Theme

**PaperMod** or **Congo** — both are minimal, fast, responsive Hugo themes with dark mode support, good typography, and portfolio/project showcase layouts. Congo in particular has a "profile" homepage layout that works well for a consulting brand page.

### Site Structure

```
ash4d.com/
├── Home (hero + value proposition + CTA)
│   ├── Tagline: e.g., "Sales Engineering Leadership · Kubernetes · AI/LLM Platforms"
│   ├── Brief positioning statement
│   └── CTA → Contact or Services
│
├── /about/
│   ├── Professional narrative (17 years SUSE, SE leadership, ecosystem building)
│   ├── Philosophy ("customer experience centered on the software")
│   ├── Photo
│   └── Link to LinkedIn profile
│
├── /expertise/
│   ├── Sales Engineering & GTM Leadership
│   ├── Kubernetes & Cloud-Native Infrastructure
│   ├── AI/LLM Platforms (Ollama, RAG, MCP, vector databases)
│   ├── Open Source & Ecosystem Strategy
│   └── MEDDPICC / Sales Methodology
│
├── /projects/
│   ├── Local AI Stack (k3s + Ollama/Qwen3 + Milvus + MCP)
│   ├── Niffler (Swift app, 24K lines)
│   ├── ash4d.com itself (k3s on GCP, Fleet-managed, Hugo)
│   └── Future: blog posts, writeups, demos
│
├── /services/  (if pursuing consulting/contract work)
│   ├── SE Team Assessment & Enablement
│   ├── AI Platform Architecture & PoC
│   ├── Pre-Sales Process Design (MEDDPICC implementation)
│   └── Partner Ecosystem Strategy
│
├── /blog/  (initially empty or 1-2 seed posts)
│   ├── Technical posts (k3s tuning, local AI stack, Fleet)
│   └── GTM/SE leadership perspectives
│
└── /contact/
    ├── Email (rick@theashfords.org or a dedicated contact form)
    ├── LinkedIn
    └── GitHub (if public)
```

### Content Strategy Note

The Technical Evangelist / Field CTO career path requires a visible portfolio of published technical content. The blog section addresses this gap directly. Seed it with 2-3 posts drawn from Rick's existing experience — the local AI stack architecture is an obvious first post. Upwork profile copy, resume materials, and the SE Leadership Operating Model doc are all sources to mine for site content in a later phase.

---

## 6. Deployment Pipeline

### Repository Structure

```
github.com/rickashford/ash4d.com/
├── site/                    # Hugo source
│   ├── config.toml
│   ├── content/
│   ├── layouts/
│   ├── static/
│   └── themes/
├── deploy/                  # Kubernetes manifests (Fleet watches this)
│   ├── namespace.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── certificate.yaml
│   └── clusterissuer.yaml
├── Dockerfile               # Multi-stage: Hugo build → nginx
├── .github/workflows/
│   └── build-and-push.yaml  # GitHub Actions: build image, push to GHCR
└── README.md
```

### Build Pipeline (GitHub Actions)

**Trigger:** Push to `main` branch (or merge of a PR).

**Steps:**

1. Checkout repo
2. Build Hugo site (`hugo --minify`)
3. Build Docker image (multi-stage: Hugo output → nginx:alpine)
4. Push image to GitHub Container Registry (`ghcr.io/rickashford/ash4d:latest` and `ghcr.io/rickashford/ash4d:<sha>`)
5. Update the image tag in `deploy/deployment.yaml` (or use a kustomize overlay / Fleet values substitution)
6. Commit the updated manifest back to `main` (or a deploy branch)

### Deployment Flow

```
Developer pushes to main
  → GitHub Actions builds Hugo site + container image
  → Pushes image to GHCR
  → Updates image tag in deploy/deployment.yaml
  → Fleet (on home k3s) detects the commit in the GitRepo
  → Fleet builds a Bundle and pushes it to the GCP downstream cluster
  → Fleet agent on GCP applies the updated Deployment
  → k3s pulls the new image and rolls the pod
  → Site is live
```

**End-to-end latency estimate:** ~2-4 minutes (GitHub Actions build ~1-2 min, Fleet polling interval default 15s, image pull ~30s).

### Alternative: Simpler Pipeline Without CI

If the GitHub Actions pipeline feels heavyweight for a personal site, there's a simpler path:

1. Build Hugo locally: `hugo --minify`
2. Build and push the image locally: `docker build -t ghcr.io/rickashford/ash4d:latest . && docker push ...`
3. Update the tag in `deploy/deployment.yaml`, commit, push
4. Fleet picks it up

This trades automation for simplicity. The CI pipeline can be added later when the publish cadence justifies it.

---

## 7. Cost Analysis

### GCP Free Tier Clarification

GCP's Always Free tier includes **1 e2-micro** instance (0.25 vCPU shared, 1 GB RAM) in us-central1 — not e2-small. An e2-micro would be extremely tight for k3s (1 GB total with OS overhead leaves almost nothing for pods). The e2-small (2 vCPU, 2 GB) is the right call but is **not covered by the Always Free tier.**

GCP's free trial provides **$300 in credits valid for 90 days** for new accounts. After the trial, billing begins immediately for resources outside the Always Free tier.

If Rick's GCP account is new, the $300 credit covers ~23 months of e2-small at the rates below. If the account is established, costs begin on day one.

### Monthly Cost Breakdown (Post-Credits)

| Item | Monthly Cost |
|---|---|
| e2-small (730 hrs, us-central1, sustained use discount) | ~$12.23 |
| Boot disk — 20 GB pd-balanced | ~$1.80 |
| Static external IP (attached to running instance) | $0.00 |
| Egress — first 1 GB free, site will likely use <10 GB/mo | ~$0.85 (est. 10 GB at $0.085/GB) |
| **GCP total** | **~$14.88/month** |

### Committed Use Discount (Optional)

A 1-year committed use discount (CUD) for the e2-small reduces compute cost by ~28%:

| Item | Monthly (CUD) |
|---|---|
| e2-small (1-year CUD) | ~$8.81 |
| Disk + egress | ~$2.65 |
| **GCP total (CUD)** | **~$11.46/month** |

### Non-GCP Costs

| Item | Cost | Frequency |
|---|---|---|
| ash4d.com domain renewal | ~$12-15 | Annual |
| Dyn.com DNS hosting | Depends on plan (check current subscription) | Monthly/Annual |
| GitHub (free tier — public repo) | $0 | — |
| Let's Encrypt certificates | $0 | — |
| Tailscale (free tier — up to 100 devices) | $0 | — |

### Total Annual Cost Estimate

| Scenario | Year 1 | Year 2+ |
|---|---|---|
| New GCP account ($300 credit) | ~$0 (credits cover ~23 months) | ~$179/year ($14.88 × 12) |
| Established GCP account, no CUD | ~$179/year | ~$179/year |
| Established GCP account, 1-year CUD | ~$137/year | ~$137/year |
| Domain + DNS (estimated) | ~$15 + DNS plan | ~$15 + DNS plan |

### Cost Reduction Options (If Needed Later)

- **Spot/preemptible instance:** ~60-70% cheaper but can be terminated with 30 seconds notice. Not ideal for a public site, but tolerable if downtime is acceptable. k3s restarts cleanly after preemption.
- **e2-micro + swap:** The Always Free e2-micro with 1-2 GB of swap space could theoretically run the optimized k3s config. It would be slow and fragile, but functional for a static site with minimal traffic. Worth testing if cost is a primary concern.
- **Cloud Run or GKE Autopilot:** A static site on Cloud Run would cost near-zero for low traffic (pay-per-request), but defeats the purpose of the k3s + Fleet architecture that demonstrates Rick's Kubernetes expertise.

---

## 8. Security Considerations

### Network / Firewall

- **Principle of least exposure:** Only ports 80, 443, and 22 are open. SSH is restricted to known sources (home IP + Tailscale CIDR). No NodePort range exposed.
- **GCP firewall rules are stateful:** Return traffic for established connections is automatically allowed; no explicit egress rules needed for outbound package updates or Tailscale.
- **Default deny:** GCP's implied deny-ingress rule blocks everything not explicitly allowed. Verify no default-allow-* rules exist in the VPC that might be overly permissive.

### k3s Hardening

- **Disable anonymous auth:** k3s disables anonymous auth by default — verify with `--anonymous-auth=false` if paranoid.
- **Rotate the node token:** After initial setup, rotate `/var/lib/rancher/k3s/server/node-token`. On a single-node cluster this is mostly hygiene, but prevents a leaked token from joining rogue nodes.
- **Read-only root filesystem on the site pod:** The nginx container serving the static site should run with `readOnlyRootFilesystem: true` in its SecurityContext, with a tmpfs mount for nginx's pid/cache files.
- **Non-root container:** Run the nginx pod as a non-root user (UID 1000+). The official `nginxinc/nginx-unprivileged` image handles this.
- **Network policies:** Deploy a default-deny NetworkPolicy in the site namespace, then explicitly allow ingress on 80/443 from the Ingress controller and egress to cert-manager/DNS for ACME challenges.
- **Pod security:** Apply the `restricted` Pod Security Standard to the site namespace:
  ```bash
  kubectl label namespace ash4d pod-security.kubernetes.io/enforce=restricted
  ```
- **Audit logging:** k3s supports audit logging via `--kube-apiserver-arg=audit-log-path=/var/log/k3s-audit.log`. On a personal site the value is marginal, but it's cheap insurance and useful for the portfolio narrative ("production-hardened k3s").

### Certificate Management

- Let's Encrypt certs auto-renew via cert-manager (30 days before expiry).
- The CAA DNS record (`0 issue "letsencrypt.org"`) prevents other CAs from issuing certs for ash4d.com.
- Monitor cert expiry with a simple cron job or cert-manager's built-in Prometheus metrics (if metrics-server is re-enabled later).
- **Fallback:** If cert-manager has issues, `certbot` in standalone mode via a systemd timer is a viable manual fallback.

### Tailscale ACLs

Rick's Tailscale ACLs should grant:

- The home k3s node full access to the GCP instance's Tailscale IP (for Fleet management, kubectl, SSH).
- Rick's personal devices SSH access to the GCP instance.
- The GCP instance access to the home k3s node's API server port (6443) — this is how the Fleet agent communicates back to the Fleet controller.
- **Deny everything else** between these nodes by default. The GCP instance should not be able to reach other devices on the tailnet (printers, NAS, etc.) and vice versa.

Example ACL snippet:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["tag:homelab"],
      "dst": ["tag:gcp:*"]
    },
    {
      "action": "accept",
      "src": ["tag:gcp"],
      "dst": ["tag:homelab:6443"]
    },
    {
      "action": "accept",
      "src": ["rick@theashfords.org"],
      "dst": ["tag:gcp:22"]
    }
  ]
}
```

### OS-Level Hardening

- **Automatic security updates:** Enable `zypper` automatic patches for security-only updates, or a systemd timer running `zypper patch --category security -y` daily.
- **Fail2ban or sshguard:** Install for SSH brute-force protection, even with firewall restrictions — defense in depth.
- **Unattended reboots:** Configure `kured` (Kubernetes Reboot Daemon) to handle node reboots after kernel updates without manual intervention. On a single-node cluster, this means brief downtime — acceptable for a personal site.
- **Remove unnecessary services:** Disable any default openSUSE services that aren't needed (cups, avahi, etc.).

---

## 9. Implementation Sequence

A suggested order of operations for when implementation begins:

| Phase | Steps | Dependencies |
|---|---|---|
| **1. Infrastructure** | Reserve static IP → create e2-small instance → apply firewall rules | GCP account + billing |
| **2. OS Setup** | SSH in → zypper update → install Tailscale → join tailnet | Phase 1 |
| **3. DNS** | Update Dyn.com A records → verify propagation | Phase 1 (need static IP) |
| **4. k3s** | Install k3s with memory-optimized flags → verify cluster health | Phase 2 |
| **5. Networking** | Install nginx Ingress controller → install cert-manager → create ClusterIssuer → verify HTTPS | Phases 3 + 4 |
| **6. Fleet** | Register GCP cluster as downstream in home Fleet → verify agent connectivity | Phases 2 + 4 (Tailscale + k3s) |
| **7. Site** | Initialize Hugo site → choose theme → scaffold content structure | None (can start anytime) |
| **8. Pipeline** | Create GitHub repo → Dockerfile → GitHub Actions workflow → Fleet GitRepo CR | Phases 6 + 7 |
| **9. Deploy** | Push to main → verify Fleet deploys → verify site loads on ash4d.com | Phase 8 |
| **10. Harden** | Network policies → pod security → audit logging → Tailscale ACLs → fail2ban → auto-updates | Phase 9 |

Phases 1-6 are the critical path. Phase 7 (site content) can be done in parallel on a laptop. Phase 10 is important but can follow the initial deployment.

---

## 10. Open Questions

- **Dyn.com DNS plan:** Confirm the current Dyn.com subscription supports the records needed (A, CAA). If Dyn.com becomes a cost or friction point, Cloudflare DNS (free tier) is a strong alternative — it also provides a CDN/caching layer that would reduce egress costs and improve performance.
- **GitHub Container Registry vs. Docker Hub:** GHCR is preferred (private images on free tier, integrated with GitHub Actions), but confirm Rick has a GitHub account and is comfortable with GHCR authentication.
- **Domain email:** If rick@ash4d.com email is desired later, that's a separate concern (Cloudflare Email Routing or Fastmail forwarding are lightweight options).
- **Analytics:** If site traffic analytics are desired, consider Plausible or Umami (self-hosted, privacy-respecting) over Google Analytics. Either can run as a second pod on the same k3s cluster, though memory impact should be evaluated.
- **Content migration:** Upwork profile copy, resume accomplishments, and the SE Leadership Operating Model doc are flagged as content sources for a later phase.
