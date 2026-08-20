# Helm chart — exec-email-copilot

Deploys the copilot as a single stateless web workload (the app serves the
product UI, the JSON API, and the benchmark surface from one container).

## Install

```bash
# Build/push the image somewhere the cluster can pull it, then:
helm install copilot helm/exec-email-copilot \
  --set image.repository=ghcr.io/you/exec-email-copilot \
  --set image.tag=v1.0.0 \
  --set secret.OPENAI_API_KEY=$OPENAI_API_KEY
```

The pod runs as uid **10001** (the uid the Dockerfile creates and chowns
`/app` to). Probes use the app's real split endpoints: `/health/live` for
liveness and `/health/ready` (DB probe, 503 on failure) for readiness.

## State

By default the app uses its zero-config SQLite store under `/app/data`
inside the container — fine for evaluation, gone on pod restart. Two ways to
keep state:

- **Postgres (recommended):** set `DATABASE_URL` in `extraEnv` and flip
  `containerSecurityContext.readOnlyRootFilesystem` to `true`.
- **PVC:** `--set persistence.enabled=true`. An init container seeds the
  volume from the image's packaged `data/` tree on first boot (DATA_DIR
  replaces the whole data root, fixtures included), and the app is pointed at
  the volume via `DATA_DIR`.

## Upgrade / rollback

```bash
helm upgrade copilot helm/exec-email-copilot --reuse-values --set image.tag=v1.1.0
helm rollback copilot 1
```

## Values worth knowing

| Value | Default | Notes |
|---|---|---|
| `replicaCount` | 2 | Stateless; safe to scale unless using the SQLite default (then use 1 or Postgres). |
| `probes.*` | `/health`, `/health/live`, `/health/ready` | startup / liveness / readiness. |
| `secret.*` | empty | Provider API keys only; model/provider selection is `extraEnv` (`MODEL_NAME`, `LLM_PROVIDER`). |
| `existingSecret` | "" | Use a pre-created Secret instead of chart-managed. |
| `persistence.enabled` | false | PVC + init-container seed + `DATA_DIR`. |
| `containerSecurityContext.readOnlyRootFilesystem` | false | Flip to `true` once the DB is external. |

CI lints the chart and renders it with defaults and with
`persistence.enabled=true` on every push (`.github/workflows/ci.yml`, job
`helm`).
