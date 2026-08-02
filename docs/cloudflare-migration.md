# ash4d.com → Cloudflare Workers

**Date:** August 2, 2026
**Status:** Runbook — repo files staged, nothing deployed, nothing torn down.
**Goal:** ash4d.com served from Cloudflare Workers static assets. GCP edge retired. No home-lab dependency in the public site's serving path.

Out of scope: the gated demo portal. Deferred deliberately, and nothing here blocks it.

---

## Current state (verified 2026-08-02)

| Thing | State |
|---|---|
| `ash4d.com`, `www.ash4d.com` | Resolve to Cloudflare proxy IPs (`172.67.129.56`, `104.21.1.124`) |
| Apex + `www` records | **Read from the API 2026-08-02.** Both are proxied `CNAME` → `a7ac2482-e55f-49ea-a352-0028a54748c3.cfargotunnel.com`, TTL auto. The site is served **through the tunnel to `sdf1`**; the GCP edge is not in the DNS path at all |
| Zone / account | zone `05edfe2501a5024ae8a87b5e1529f660`, account `2527df8e8195866b65be984b3dc14c33` |
| Origin pod | `ash4d-origin/origin`, `ghcr.io/darthzen/ash4d:56403cb`, running on `sdf1` |
| Fleet | GitRepo `ash4d-site` in `fleet-default` on the **rancher** context (khyron), 1/1 bundle ready |
| Tunnel | `cloudflare-tunnel/cloudflared`, 2 replicas, tunnel `a7ac2482-e55f-49ea-a352-0028a54748c3`. Ingress rules cover `tunnel-test`, `ash4d.com`, `www`, `buzz`, `ollama` |
| Cloudflare account | Zero Workers deployed |

The tunnel stays. `buzz.ash4d.com` and `ollama.ash4d.com` still need it — only
the `ash4d.com` / `www` / `tunnel-test` rules come out.

`registry.ash4d.com` does **not** use the tunnel, contrary to earlier drafts of
this doc: it is an unproxied `A` record to `192.168.7.150`, reachable only from
the LAN or tailnet. Two consequences — the tunnel edit cannot break it, and the
zone publishes an internal RFC1918 address to anyone who queries it. Worth
deciding separately from this migration.

Because apex and `www` are tunnel CNAMEs, **the tunnel ingress rules are the
rollback path**. Do not touch step 7 until step 4 has passed.

---

## Staged in the repo (uncommitted)

| File | Purpose |
|---|---|
| `wrangler.jsonc` | Worker config: `./site`, `html_handling: "none"`, custom domains for apex + www |
| `src/index.js` | Ten-line entrypoint mapping `/` → `/index.html`. Required by `html_handling: "none"`, which otherwise leaves `/` unresolved. Delete both this and `main` if html_handling ever returns to a trailing-slash mode |
| `.github/workflows/deploy-worker.yaml` | Push to `main` touching `site/**` → `wrangler deploy` |
| `site/_headers` | Security headers, long cache on `/img/*`. Verified to apply through the `ASSETS` binding |

The `www` → apex 301 is **not** a repo file. Workers static assets do not support
domain-level redirects — `_redirects` matches a path, not a hostname, so a rule
like `https://www.ash4d.com/* https://ash4d.com/:splat 301` silently never fires.
It is a zone Redirect Rule instead; see step 3.

Deleted at step 6, not before: `Dockerfile`, `deploy/`, `.github/workflows/build-and-push.yaml`.

---

## 1. API token

Cloudflare dashboard → My Profile → API Tokens → **Edit Cloudflare Workers** template, scoped to the ash4d.com zone. It carries what this needs: Workers Scripts:Edit, Workers Routes:Edit, Zone DNS:Edit.

Store it under `~/Developer/keys/cloudflare/` alongside the existing tokens. It is a `cfut_`-prefixed profile token, so it comes from the profile page, not the account page.

Then in GitHub → `darthzen/ash4d.com` → Settings → Secrets → Actions:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

## 2. Deploy to workers.dev first — no DNS impact

Comment the `routes` block out of `wrangler.jsonc`, then:

```bash
cd ~/Developer/ash4d.com
npx wrangler deploy
```

Verify the whole site on the `ash4d-com.<subdomain>.workers.dev` URL wrangler prints: index, `/fossa-mcp.html`, `/resume.pdf`, images, CSS. Check response headers show the `_headers` entries.

Nothing about ash4d.com has changed at this point.

### Done — 2026-08-02

Deployed to `https://ash4d-com.rick-252.workers.dev`, 35 assets, no routes
attached. Every path compared against the live tunnel-served site: `/`,
`/index.html`, `/fossa-mcp.html`, `/style.css`, `/resume.pdf`,
`/img/headshot.jpg` and an unknown path all return **matching status codes**.
All five `_headers` security rules apply, `/img/*` gets
`public, max-age=31536000, immutable`, `/style.css` and `/resume.pdf` get
`max-age=3600`, and `/_headers` itself is not served.

Two deliberate, understood differences:

- **The homepage is 245 bytes smaller on `workers.dev`.** That is Cloudflare
  **Email Address Obfuscation**, a zone setting that rewrites the `mailto:`
  and injects `email-decode.min.js`. `workers.dev` is outside the zone so it
  does not apply. It resumes automatically at cutover. The repo's `index.html`
  is byte-for-byte what the origin serves.
- **A 404 returns an empty body** where nginx returned a 153-byte page. See
  the `site/404.html` follow-up.

**Beware propagation.** A fresh deploy serves stale responses for roughly a
minute, including cached redirects from a previous config. Verify with a
cache-busting query (`?cb=$RANDOM`) or the results will lie to you — this cost
two false diagnoses during the first run.

## 3. Attach the custom domains — this is the cutover

The rollback target, already read and confirmed — recreate these exactly if the
cutover has to be undone:

| Name | Type | Content | Proxied | TTL |
|---|---|---|---|---|
| `ash4d.com` | `CNAME` | `a7ac2482-e55f-49ea-a352-0028a54748c3.cfargotunnel.com` | yes | auto |
| `www.ash4d.com` | `CNAME` | `a7ac2482-e55f-49ea-a352-0028a54748c3.cfargotunnel.com` | yes | auto |

The zone's `MX` and `TXT` records at the apex (Google Workspace mail, two site
verifications) are untouched by a custom domain, which replaces only the
address record. No action needed, but confirm they survive after step 3.

Restore the `routes` block and redeploy. Wrangler will report the conflicting DNS records and ask to override them. Accept — that is the cutover. It provisions the certificate and repoints both hostnames at the Worker.

Propagation is seconds, not minutes; the records already carry a 300s TTL.

### The www → apex redirect

Both hostnames now serve the same assets, so `www` needs a 301 or the site is
duplicated across two origins. Cloudflare dashboard → ash4d.com → Rules →
Redirect Rules → **Create rule**, single redirect:

- **When**: `Hostname` `equals` `www.ash4d.com`
- **Then**: static/dynamic → `concat("https://ash4d.com", http.request.uri.path)`
- Status **301**, **preserve query string** on

This runs in the rules phase, ahead of Workers, so it fires before the `www`
custom domain ever reaches the assets. Keep `www` in `routes` regardless — it is
what holds the hostname on Cloudflare.

Doing this through the API instead needs a token scoped to *Zone → Config Rules
→ Edit*, which the **Edit Cloudflare Workers** template does not carry. The
dashboard is the shorter path.

## 4. Verify — the real test

```bash
curl -sI https://ash4d.com | head -20
curl -sI https://www.ash4d.com          # expect 301 → https://ash4d.com
curl -sI 'https://www.ash4d.com/fossa-mcp.html?a=1'   # path and query survive
```

If `www` returns 200 with page content rather than a 301, the Redirect Rule in
step 3 is missing or its expression does not match.

Then prove the lab is genuinely out of the path:

```bash
kubectl -n ash4d-origin scale deploy/origin --replicas=0
curl -s https://ash4d.com | head -5     # still serves
```

Leave the origin at zero. It stays that way until step 6 deletes it.

Rollback at any point before step 5: restore the previous DNS records for apex and www, scale the origin back to 1.

## 5. Retire the GCP edge

Resource names below are the ones the July plan specified. Confirm against reality first — none of them are verified.

```bash
gcloud compute instances list
gcloud compute addresses list
gcloud compute firewall-rules list
```

Then, in order:

```bash
gcloud compute instances delete <instance>          # us-central1-a
gcloud compute addresses delete ash4d-ip --region=us-central1
gcloud compute firewall-rules delete allow-http allow-https allow-ssh allow-icmp
```

A reserved static IP that is no longer attached to a running instance bills at ~$7.30/month. Release it, do not just stop the instance.

Also:

- Tailscale admin console → remove the GCP node from the tailnet.
- Rancher/Fleet → delete the downstream cluster registration for the GCP cluster in `fleet-default`.
- GCP billing → confirm the project drops to zero over the next day.

## 6. Remove the lab-side site plumbing

Fleet controller lives on the **rancher** context:

```bash
kubectl --context rancher -n fleet-default delete gitrepo ash4d-site
kubectl delete namespace ash4d-origin          # default context = sdf1
```

Delete the GitRepo before the namespace, or Fleet re-creates what you just deleted.

DNS: remove the `tunnel-test.ash4d.com` record. **There is no
`origin.ash4d.com` record** — earlier drafts of this step said to delete one.
Confirmed against the live zone 2026-08-02.

Repo, in one commit:

```
git rm -r deploy/ Dockerfile .github/workflows/build-and-push.yaml
```

Optionally delete the `ghcr.io/darthzen/ash4d` package in GitHub Packages.

## 7. lab-fleet edit — handoff, not applied here

`lab-fleet` is entire-tracked, so this change does not get written from a Cowork session. Run it through Claude Code.

File: `23-cloudflare-tunnel/` — the `config.yaml` that becomes the `cloudflared` ConfigMap.

Remove three ingress rules: `tunnel-test.ash4d.com`, `ash4d.com`, `www.ash4d.com`. Keep `buzz.ash4d.com`, `ollama.ash4d.com`, and the trailing `http_status:404` catch-all.

After Fleet reconciles, `kubectl -n cloudflare-tunnel rollout restart deploy/cloudflared` if the pods do not pick the ConfigMap change up on their own.

## 8. Docs that go stale

`README.md` and `docs/infrastructure.md` describe the GCP caching edge and Fleet-managed site as current architecture. Both become wrong at step 5. Rewrite them after the teardown is verified, not before — status in a doc is a snapshot and this one is about to move.

---

## Follow-ups

- Pin the wrangler version in the workflow once a version deploys cleanly.
- `site/404.html` plus `"not_found_handling": "404-page"` if a custom 404 is
  wanted. Currently a 404 returns an empty body; the origin returned nginx's
  153-byte page.
- `html_handling` is set to `"none"` to keep `/fossa-mcp.html` serving at its
  own URL, matching the origin. The cost is `src/index.js` and a Worker
  invocation per request. The alternative — dropping to the default
  `auto-trailing-slash` — makes the Worker assets-only again but 307s every
  `.html` URL to its extensionless form.
- `Strict-Transport-Security` in `_headers` deliberately omits `includeSubDomains`, so it does not commit the lab subdomains to HTTPS-only. Add it once every `*.ash4d.com` host is confirmed HTTPS-only.
- The www Redirect Rule is dashboard state, not repo state. Nothing in this repo
  will tell you if it is deleted. Worth a note in `docs/infrastructure.md` at
  step 8.
