# ash4d.com

Personal site for Rick Ashford. Static HTML/CSS, served by nginx-unprivileged on a
single-node k3s edge cluster (GCP e2-small, openSUSE MicroOS), deployed via Fleet
GitOps from the home lab cluster over Tailscale.

## Pipeline

```
push to main (site/**)
  → GitHub Actions builds image → ghcr.io/darthzen/ash4d:<sha>
  → workflow pins the tag in deploy/deployment.yaml and commits
  → Fleet (home cluster) sees the commit, pushes Bundle to the edge
  → edge k3s rolls the deployment
```

## Layout

- `site/` — the site itself (index.html, style.css)
- `Dockerfile` — nginx-unprivileged + static files
- `deploy/` — Kubernetes manifests watched by Fleet
- `.github/workflows/` — image build + manifest pin

## TODO

- [ ] Add `site/resume.pdf` (Download Resume button target)
- [ ] Privacy Policy / Terms of Service pages or remove footer links
- [ ] Verify LinkedIn URL (assumed /in/rickashford)
