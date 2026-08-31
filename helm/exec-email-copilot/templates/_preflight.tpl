{{/*
Refuse to render a configuration that cannot safely run.

This chart passed `helm lint` and `helm template` while being impossible to
install correctly, which is a worse state than having no chart: linting knows
nothing about leader election, shared SQLite files, or the fact that the
default signing secret is a publicly-known constant in this repository.

Every check below corresponds to a real way the previous defaults broke a real
deployment. They fail at render time, so `helm install` stops before it creates
anything rather than after — and the message says what to set.
*/}}

{{- define "exec-email-copilot.preflight" -}}

{{- $env := dict -}}
{{- range .Values.extraEnv }}{{- $_ := set $env .name (.value | toString) }}{{- end }}
{{- $syncOn := eq (($env.SYNC_WORKER_ENABLED | default "false") | lower) "true" -}}
{{- $hasDatabaseUrl := or (hasKey $env "DATABASE_URL") .Values.existingSecret -}}

{{/* 1. The signing secret. Unset, app/core/config.py falls back to
      DEV_AUTH_SECRET — a constant published in this repo — which signs every
      session and license key AND derives the key that encrypts customers'
      mailbox OAuth tokens. Anyone who can read the source can mint a session. */}}
{{- if and (not .Values.existingSecret) (not .Values.secret.AUTH_SECRET_KEY) }}
{{- fail "AUTH_SECRET_KEY is required. Unset, the app signs sessions and licenses — and derives the mailbox-token encryption key — from a constant published in this repository. Generate one with:\n  python -c \"import secrets; print(secrets.token_urlsafe(48))\"\nthen pass --set secret.AUTH_SECRET_KEY=... or point existingSecret at a Secret containing it.\nBack it up: rotating it invalidates every session, every issued license key, and every stored mailbox token." }}
{{- end }}

{{/* 2. Two replicas plus the background sync worker, with no leader election
      and no distributed lock anywhere in app/saas/sync_worker.py, means every
      connected mailbox is synced twice on every sweep — duplicate provider
      calls against a rate-limited API, and a race on the idempotency check. */}}
{{- if and (gt (int .Values.replicaCount) 1) $syncOn }}
{{- fail "replicaCount > 1 with SYNC_WORKER_ENABLED=true would sync every mailbox once per replica: there is no leader election or distributed lock in app/saas/sync_worker.py. Either keep replicaCount: 1, or set SYNC_WORKER_ENABLED=false in extraEnv and run the worker as a separate single-replica Deployment." }}
{{- end }}

{{/* 3. Without DATABASE_URL the app uses a local SQLite file. Two replicas
      behind one Service therefore hold two different databases and a user's
      session works or fails depending on which pod answered. The PVC does not
      rescue this either: it is provisioned ReadWriteOnce. */}}
{{- if and (gt (int .Values.replicaCount) 1) (not $hasDatabaseUrl) }}
{{- fail "replicaCount > 1 without DATABASE_URL: the default is a local SQLite file, so each replica would hold a different database behind one Service and a request would succeed or fail depending on which pod answered. Set DATABASE_URL in extraEnv (Postgres), or keep replicaCount: 1." }}
{{- end }}

{{/* 4. A ReadWriteOnce PVC cannot be mounted by pods on different nodes, so a
      multi-replica Deployment using one is a rollout that hangs. */}}
{{- if and .Values.persistence.enabled (gt (int .Values.replicaCount) 1) }}
{{- fail "persistence.enabled with replicaCount > 1: the PVC is ReadWriteOnce and cannot be mounted by pods on different nodes, so the rollout will hang. Use DATABASE_URL with replicaCount > 1, or keep replicaCount: 1." }}
{{- end }}

{{/* 5. ENVIRONMENT=production is what makes the app hard-fail on unsafe
      config instead of warning into a log nobody reads. A chart that ships
      "development" is a chart that ships the warnings-only posture. */}}
{{- if ne ($env.ENVIRONMENT | default "") "production" }}
{{- fail "ENVIRONMENT must be \"production\" in extraEnv. It is what makes the app refuse to boot on unsafe configuration rather than log a warning and serve traffic anyway." }}
{{- end }}

{{/* 6. CORS_ORIGINS "*" plus cookie sessions. app/main.py already refuses to
      send credentials with a wildcard origin, so this does not leak the
      session — but it does mean the chart's default cannot serve a browser
      client at all, while looking permissive. Say so at install time. */}}
{{- if eq ($env.CORS_ORIGINS | default "*") "*" }}
{{- fail "CORS_ORIGINS must be pinned to your public origin (e.g. https://copilot.example.com). With \"*\" the app disables credentialed CORS entirely, so a browser client cannot hold a session — the wildcard looks permissive and is the most restrictive setting available." }}
{{- end }}

{{/* 7. ALLOWED_HOSTS unset means "*", which leaves Host-header injection able
      to influence the absolute URLs in password-reset links. */}}
{{- if eq ($env.ALLOWED_HOSTS | default "*") "*" }}
{{- fail "ALLOWED_HOSTS must list your public hostname(s). Left at \"*\", a forged Host header can influence the absolute URLs the app builds — password-reset links and the OAuth redirect URI." }}
{{- end }}

{{- end -}}
