"""API integration test against a running Agent Relay and its real database.

Unlike ``test_agent_relay.py`` (which drives the ASGI app in-process against a
scratch SQLite file), this test talks HTTP to a deployed instance. Point it at
one with ``RELAY_BASE_URL``; without that variable the module is skipped so the
unit suite still runs on a machine with nothing deployed.

Covers acceptance scenario 1 from SPEC.md: register two agents, one sends a
task, the other claims and completes it, and the sender reads the result.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

BASE_URL = os.getenv("RELAY_BASE_URL")

pytestmark = pytest.mark.skipif(
    not BASE_URL, reason="set RELAY_BASE_URL to run integration tests against a running API"
)


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=f"{BASE_URL}/api/v1", timeout=40.0) as c:
        yield c


def _wait_for_ready(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{BASE_URL}/ready", timeout=5.0)
            if resp.status_code == 200:
                return
            last = f"status {resp.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(2)
    raise AssertionError(f"API never became ready at {BASE_URL}: {last}")


def _register(client: httpx.Client, name: str) -> tuple[str, dict[str, str]]:
    resp = client.post("/agents", json={"name": name})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["agent_id"], {"Authorization": f"Bearer {body['token']}"}


def test_ready():
    _wait_for_ready()


def test_task_round_trip(client):
    _wait_for_ready()

    sender_id, sender_auth = _register(client, "alice-sender")
    worker_id, worker_auth = _register(client, "bob-uppercase")

    payload = "hello from the integration test"
    created = client.post("/tasks", json={"to": worker_id, "input": payload}, headers=sender_auth)
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "queued"

    claimed = client.post(
        "/tasks/claim", json={"worker_id": "integration-runner", "wait_seconds": 30}, headers=worker_auth
    )
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    assert claim["task_id"] == task_id
    assert claim["from"] == sender_id
    assert claim["input"] == payload
    assert claim["attempt"] == 1

    # The sender sees the task as processing while the lease is active.
    mid = client.get(f"/tasks/{task_id}", headers=sender_auth)
    assert mid.status_code == 200
    assert mid.json()["status"] == "processing"

    result = payload.upper()
    done = client.post(
        f"/tasks/{task_id}/complete",
        json={"claim_token": claim["claim_token"], "output": result},
        headers=worker_auth,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"

    # This is the answer the homework asks about: after the recipient submits
    # its result, the sender sees the task as "completed".
    final = client.get(f"/tasks/{task_id}", headers=sender_auth)
    assert final.status_code == 200
    body = final.json()
    assert body["status"] == "completed"
    assert body["output"] == result
    assert body["error"] is None
    assert body["attempt_count"] == 1
    assert body["finished_at"] is not None

    attempts = client.get(f"/tasks/{task_id}/attempts", headers=sender_auth)
    assert attempts.status_code == 200
    items = attempts.json()["items"]
    assert len(items) == 1
    assert items[0]["outcome"] == "completed"
    assert items[0]["worker_id"] == "integration-runner"
    assert "claim_token" not in items[0]


def test_other_agents_cannot_read_the_task(client):
    _wait_for_ready()

    _, sender_auth = _register(client, "carol-sender")
    worker_id, _ = _register(client, "dave-worker")
    _, stranger_auth = _register(client, "eve-stranger")

    created = client.post("/tasks", json={"to": worker_id, "input": "private"}, headers=sender_auth)
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    denied = client.get(f"/tasks/{task_id}", headers=stranger_auth)
    assert denied.status_code == 404
    assert "private" not in denied.text
