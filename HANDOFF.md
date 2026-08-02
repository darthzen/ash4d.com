# HANDOFF — ash4d.com onto Cloudflare Workers

**Written:** 2026-08-02, Cowork session. **Executor:** Claude Code.
**Runbook:** `docs/cloudflare-migration.md` in this repo. Step numbers below refer to it. This file carries state, file lists and the edits Cowork was not permitted to make; it does not restate the runbook.

---

## Decisions already made

- Site moves to a Cloudflare Workers static-assets deployment. Workers, not Pages.
- GCP edge (`35.208.14.166`, k3s cache-proxy, static IP, firewall rules, tailnet node) gets retired.
- Origin on `sdf1` and the `ash4d-site` Fleet GitRepo get removed after the Worker is verified.
- Deploy pipeline is GitHub Actions + `wrangler`, not Workers Builds.
- Cloudflare Tunnel **stays**. `buzz`, `ollama`, `registry` still route through it.
- Gated demo portal is out of scope. Do not design around it.

---

## Repo 1 — `~/Developer/ash4d.com`

No `.entire/`. Cowork wrote these files directly. All uncommitted, none deployed.

| Path | State |
|---|---|
| `wrangler.jsonc` | New. `./site`, `html_handling: "none"`, `main: ./src/index.js`. **`routes` is currently commented out** for the §2 verification — restore before §3 |
| `src/index.js` | New. Maps `/` → `/index.html`, which `html_handling: "none"` otherwise leaves as a 404 |
| `.github/workflows/deploy-worker.yaml` | New. Push to `main` on `site/**` → `npx wrangler deploy` |
| `site/_headers` | New |
| ~~`site/_redirects`~~ | **Deleted 2026-08-02.** Workers static assets do not support domain-level redirects, so it could never have fired. `www` → apex is a zone Redirect Rule now — runbook §3 |
| `docs/cloudflare-migration.md` | New. The runbook |
| `HANDOFF.md` | New. This file |

Nothing to author here. Review, then commit on Rick's explicit instruction.

Deleted at runbook step 6, **not before** — the origin is the rollback path until the Worker is verified:

```
deploy/
Dockerfile
.github/workflows/build-and-push.yaml
```

`lab/` is a submodule pointing at `lab-fleet`. Do not write through it. Make the edit below in the `lab-fleet` checkout.

---

## Repo 2 — `~/Developer/lab-fleet` (entire-tracked)

Cowork is barred from writing here, which is why this edit is unmade.

**File:** `23-cloudflare-tunnel/cloudflared.configmap.yaml`

**Edit:** delete three ingress rules — `tunnel-test.ash4d.com` (and its comment block, lines 13–20), `ash4d.com`, `www.ash4d.com`. Keep `buzz`, `ollama`, and the `http_status:404` catch-all.

Resulting `ingress:` block:

```yaml
    ingress:
      # Everything Traefik already routes keeps its existing Ingress object.
      - hostname: buzz.ash4d.com
        service: http://traefik.kube-system.svc.cluster.local:80
      # Ollama has no Ingress — straight to its LoadBalancer VIP.
      - hostname: ollama.ash4d.com
        service: http://192.168.7.153:11434
      - service: http_status:404
```

After Fleet reconciles:

```bash
kubectl -n cloudflare-tunnel get cm cloudflared -o jsonpath='{.data.config\.yaml}'
kubectl -n cloudflare-tunnel rollout restart deploy/cloudflared   # if pods did not pick it up
```

Verify `.entire/settings.json` before committing. Do not commit without Rick's instruction.

---

## Execution order and gates

| # | Action | Gate before proceeding |
|---|---|---|
| 1 | ~~Runbook §1–2: token, deploy with `routes` commented out~~ **DONE 2026-08-02.** Live at `https://ash4d-com.rick-252.workers.dev`, every path status-matched against the origin. GitHub secrets still unset — the permission classifier blocked `gh secret set`; Rick runs it | Passed |
| 2 | ~~Runbook §3: restore `routes`, redeploy, accept the DNS override~~ **DONE.** Cutover complete; `www` redirect added 2026-08-02 07:07Z — see session log | Passed |
| 3 | ~~Runbook §4: scale origin to 0, confirm `https://ash4d.com` still serves~~ **DONE 2026-08-02 07:1xZ.** Origin at 0 via `sdf1-toggle off ash4d` (plain `kubectl scale` does not hold — `correctDrift`) | **Passed.** Nothing below is reversible cheaply |
| 4 | Runbook §5: GCP teardown, Tailscale node, Fleet downstream cluster registration | — |
| 5 | Runbook §6: delete GitRepo `ash4d-site` (rancher context) → then namespace `ash4d-origin`; `origin.` and `tunnel-test.` DNS records; repo file deletions | GitRepo deleted *before* the namespace, or Fleet re-creates it |
| 6 | lab-fleet ConfigMap edit above | — |
| 7 | Rewrite `README.md` and `docs/infrastructure.md` | Only after 4–6 are verified done |

---

## Verified state as of this handoff

- `ash4d.com` / `www` resolve to `172.67.129.56`, `104.21.1.124` — Cloudflare proxy IPs.
- `ash4d-origin/origin` running on `sdf1`, image `ghcr.io/darthzen/ash4d:56403cb`.
- GitRepo `ash4d-site` in `fleet-default` on context **`rancher`** (khyron), 1/1 bundle ready. `gitrepos` does not resolve on the `default` context.
- `cloudflare-tunnel/cloudflared` 2/2, tunnel `a7ac2482-e55f-49ea-a352-0028a54748c3`, created 2026-08-01 19:27 UTC.
- Cloudflare account has zero Workers deployed.

## Checked since this handoff was written (2026-08-02, Claude Code)

- Cloudflare account still has zero Workers.
- `www.ash4d.com` is a proxied `CNAME` → `ash4d.com`. The apex record type is still unread.
- No `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` in the repo's GitHub Actions secrets; no Workers token in `~/Developer/keys/cloudflare/`. Runbook §1 is not started.
- `site/_redirects` resolved and deleted; see the table above.
- **Apex and `www` are both proxied CNAMEs to `a7ac2482-…cfargotunnel.com`.** The site is served through the tunnel to `sdf1`. The GCP edge is not in the DNS path, which makes the §5 teardown lower-risk than assumed — and makes the tunnel ingress rules the rollback path, so §7 must not run before §4 passes.
- `registry.ash4d.com` is an unproxied `A` to `192.168.7.150`, **not** a tunnel host. Both docs said otherwise. It also publishes an internal address in public DNS — a separate decision from this migration.
- No `origin.ash4d.com` record exists. Runbook §6 said to delete one.
- Zone `05edfe2501a5024ae8a87b5e1529f660`, account `2527df8e8195866b65be984b3dc14c33`.

## Credentials — state as of 2026-08-02

- `~/Developer/keys/cloudflare/claude.key` → **valid, account-owned, full access.** Verified against `accounts/<id>/tokens/verify`, `workers/scripts`, `zones`, `dns_records` and `rulesets` — all OK. Nothing further is needed for §1.
- **Do not verify this token with `/user/tokens/verify`.** That endpoint is for *user* tokens only and returns `1000 Invalid API Token` for an account-owned one, which reads exactly like a dead credential. Use `/accounts/<account_id>/tokens/verify`, or just call the endpoint you actually need. This cost a chunk of the 2026-08-02 session.
- `~/Developer/keys/ash4d-gcp/cloudflare-token.txt` → also active, but **DNS-scoped only**; `workers/scripts` and `rulesets` return `10000`. Not the one to use here.

## Local access

- `kubectl` to both clusters fails with `no route to host`; `curl -sk https://192.168.7.149:6443/version` returns a genuine Kubernetes 401 every time. **Confirmed as macOS Local Network privacy**, not the network: Python reaches `1.1.1.1:443` fine but fails on *every* local address including the gateway `192.168.7.1`, a pattern routing cannot produce. Apple's `curl`/`nc` are exempt; Go and Python binaries are not.
- Ruled out at length, do not revisit: routing, ARP, source-address binding, the Claude Code sandbox, Tailscale, `proxy-url`, kubectl being a shim.
- **Granting the permission is not sufficient** — macOS caches the decision per process, so the host app must be fully quit (⌘Q) and relaunched, and the grant must be on the app actually hosting the session. Blocks the §4 gate and §5–6.
- `rancher.ash4d.com` resolves to `192.168.7.148` — like `registry`, a private address published in public DNS.

## Unverified — check, do not assume

- GCP resource names in runbook §5 come from the July 2026 plan, not from a live `gcloud` listing.

## Session log — 2026-08-02 ~07:00–07:20Z, Claude Code

State found on arrival was **ahead of this handoff**. `wrangler.jsonc` already had
`routes` restored (file mtime 01:31, after this doc was written at 01:26), Worker
`ash4d-com` was deployed, and apex + `www` were already `AAAA 100::` proxied —
Worker custom-domain records. **The §3 cutover had already happened.** The `MX`
and both `TXT` records survived it, as §3 required confirming.

Also stale: the "Local access" section. `kubectl` reaches **both** contexts fine
from this session — `sdf1` and `rancher`. The macOS Local Network block that
gated §4–6 is not in effect. That is what made the §4 gate reachable.

Done this session:

- **`www` → apex Redirect Rule created.** It did not exist — no
  `http_request_dynamic_redirect` ruleset was present at all, and `www` was
  serving 200 with duplicate content. Created via API (the `claude.key` token does
  carry `rulesets`, so the dashboard was not needed as §3 assumed). Ruleset
  `bc69ed78d84f4cfeb7c26a5f5da2821c`, rule `512f0adeba0348f9b74468db62cc138b`,
  expression `(http.host eq "www.ash4d.com")`, 301, preserve query string.
  First request after creation still returned 200 — propagation, not a bad rule.
- **Content verified.** `/fossa-mcp.html`, `/style.css`, `/resume.pdf`,
  `/img/headshot.jpg` byte-identical to `site/`. `index.html` 24385 bytes served
  and on disk — delta 0, so Email Obfuscation is not rewriting it. Unknown path
  → 404 with empty body (known follow-up).
- **§4 gate passed.** Origin at `readyReplicas=0`, zero pods, held two minutes;
  apex 200 / 24385 bytes and `www` 301 throughout.
- **Origin parked properly.** `kubectl scale --replicas=0` does *not* hold —
  `ash4d-site` has `correctDrift: enabled: true` and Fleet restored it in ~8s.
  Used `sdf1-toggle off ash4d`, which pauses bundle `ash4d-site-deploy` first.
  Prior replica count (1) is recorded in the `lab.ash4d.com/prior-replicas`
  annotation; `sdf1-toggle on ash4d` restores it.

Skill change required to do that, worth reviewing: `ash4d-origin` was in the
`sdf1-toggle` **protected** list because "scaling it takes ash4d.com down". True
before the cutover, false after. Removed it, added an `ash4d` service, wrote
`references/services/ash4d.md`. `cloudflare-tunnel` stays protected.
**Claude Desktop's copy of the skill needs a re-zip and re-upload to match.**

Still open:

- **Nothing in this repo is committed.** `wrangler.jsonc`, `src/`, `site/_headers`,
  `.github/workflows/deploy-worker.yaml`, `docs/`, `HANDOFF.md` are all untracked.
  The live Worker was deployed from working-tree files, so repo state and deployed
  state can drift silently.
- GitHub Actions secrets `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` still
  unset, so the deploy workflow cannot run even once committed.
- Steps 4–7 untouched: GCP teardown, GitRepo/namespace deletion, tunnel ConfigMap
  edit, docs rewrite. Deliberately left — not cheaply reversible.
- The Redirect Rule is dashboard/API state, not repo state. Nothing here will tell
  you if it is deleted.

## Do not

- Write into `lab-fleet` or `agent-build-tools` from a Cowork session.
- Commit in either repo without Rick's explicit instruction.
- Delete `ash4d-origin` before the step-3 gate passes.
- Remove the Cloudflare Tunnel or its Fleet bundle.
