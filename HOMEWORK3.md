# Homework 3 — Test, Containerize, and Deploy

[AI Dev Tools Zoomcamp 2026](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp), Module 3.

Agent Relay taken from the SQLite starter to a containerized app running on a
local Kubernetes cluster, with an integration test and a CI pipeline that only
deploys when the tests pass.

## What was added

| Path | Purpose |
|------|---------|
| `Dockerfile` | Builds `agent-relay:local`, runs uvicorn on `0.0.0.0:8000` as a non-root user |
| `.dockerignore` | Keeps the venv, git history and local databases out of the build context |
| `compose.yaml` | Runs the API together with PostgreSQL 16, with a healthcheck gate |
| `test_integration.py` | API integration test over HTTP against a running instance and its real database |
| `k8s/00-namespace.yaml` | The `agent-relay` namespace |
| `k8s/10-postgres.yaml` | PostgreSQL Deployment, Service, PVC and readiness/liveness probes |
| `k8s/20-api.yaml` | Agent Relay Deployment (2 replicas), Service and probes |
| `.github/workflows/ci.yml` | Tests against PostgreSQL, builds a uniquely tagged image, deploys to kind only on green |
| `database.py` | The one change to the starter: a PostgreSQL writer transaction |

## The PostgreSQL port

The starter isolates its only SQLite-specific behaviour in
`database.immediate_transaction`. SQLite serializes writers with
`BEGIN IMMEDIATE`, which PostgreSQL does not have. The port takes a
transaction-scoped advisory lock instead:

```python
if _is_sqlite(DATABASE_URL):
    connection.exec_driver_sql("BEGIN IMMEDIATE")
elif _is_postgres(DATABASE_URL):
    connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_WRITER_LOCK_KEY})")
```

This keeps the guarantee the protocol depends on — one active lease per task,
even with several API processes claiming concurrently — and the lock is released
automatically on commit or rollback. The HTTP protocol, task lifecycle and
delivery semantics in `SPEC.md` are unchanged, and the starter's own test suite
still passes untouched.

## Running it

Compose:

```bash
docker compose up --build
RELAY_BASE_URL=http://127.0.0.1:8080 uv run pytest test_integration.py -q
```

Kubernetes with kind:

```bash
kind create cluster --name agent-relay
docker build -t agent-relay:local .
kind load docker-image agent-relay:local --name agent-relay
kubectl apply -f k8s/
kubectl -n agent-relay rollout status deployment/agent-relay
kubectl -n agent-relay port-forward svc/agent-relay 8081:8000
RELAY_BASE_URL=http://127.0.0.1:8081 uv run pytest test_integration.py -q
```

CI locally:

```bash
act push -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-daemon-socket /var/run/docker.sock
```

## Verification

- Starter unit suite: 4 passed
- Integration test: 3 passed, against the container, the Compose stack and the kind cluster
- Data confirmed in PostgreSQL, with the completed task and its `HELLO FROM THE INTEGRATION TEST` output
- Full `act` run: `test` job green, then `deploy` builds, loads into kind and rolls out
- Dashboard heading changed to `Agent Relay v2` and redeployed through the pipeline

## Notes

The host used for this work already had port 8000 taken by another project, so
Compose publishes `8080:8000` and the CI job runs the API on 8001. Neither
changes the container's own port, which stays 8000.

## Answers

1. Agents claim tasks from a DB through an HTTP API
2. `completed`
3. `-p`
4. `postgres`
5. Deployment
6. Keep the existing version running and stop the deployment
