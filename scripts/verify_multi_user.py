"""Low-rate, no-provider acceptance against a disposable local Docker project.

Seed is a trusted fixture operation against CHEFMATE_TEST_APP_DSN. It does not
weaken HTTP registration/login rate limits or add an application test endpoint.
Cookie credentials stay in the caller-selected private state file. Metrics never
include credentials, request bodies, or personal data. Only loopback HTTP is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import statistics
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def now():
    return datetime.now(UTC).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("fixture_kind") != "chefmate-disposable-multiuser":
        raise ValueError("State does not identify the disposable ChefMate fixture")
    return value


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = Counter()
        self.latencies = deque(maxlen=10000)
        self.maximum_ms = 0.0

    def record(self, status, elapsed):
        with self.lock:
            self.counts[str(status)] += 1
            self.latencies.append(elapsed * 1000)
            self.maximum_ms = max(self.maximum_ms, elapsed * 1000)

    def snapshot(self):
        with self.lock:
            values = sorted(self.latencies)
            return {"requests": sum(self.counts.values()), "status_counts": dict(self.counts),
                    "latency_sample_count": len(values),
                    "latency_quantile_scope": "last_10000_mixed_requests",
                    "latency_ms_p50": round(statistics.median(values), 2) if values else None,
                    "latency_ms_p95": round(values[min(len(values) - 1, int(len(values) * .95))], 2) if values else None,
                    "latency_ms_max": round(self.maximum_ms, 2) if values else None}


class Http:
    def __init__(self, base, token, metrics):
        parsed = urlsplit(base)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.query or parsed.fragment:
            raise ValueError("Acceptance only supports a loopback HTTP origin")
        self.base, self.token, self.metrics = base.rstrip("/"), token, metrics
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, *, body=None, raw=None, expect=200, headers=None):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else raw
        request = Request(self.base + path, data=payload, method=method, headers={
            "Cookie": "chefmate_session=" + self.token, "Origin": self.base,
            "Content-Type": "application/json" if body is not None else "application/octet-stream",
            **(headers or {})})
        started = time.monotonic()
        try:
            response = self.opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            content, status = response.read(), response.status
            cookie = response.headers.get("set-cookie", "")
            if cookie.startswith("chefmate_session="):
                self.token = cookie.split(";", 1)[0].split("=", 1)[1]
            content_type = response.headers.get("content-type", "")
        self.metrics.record(status, time.monotonic() - started)
        if status != expect:
            raise AssertionError(f"{method} {path} returned {status}; expected {expect}")
        return json.loads(content) if "application/json" in content_type else content


def seed(args):
    if not args.confirm_disposable:
        raise ValueError("Seed requires --confirm-disposable and a fresh, isolated project")
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not dsn:
        raise ValueError("CHEFMATE_TEST_APP_DSN must explicitly select the disposable app database")
    from chefmate_playground.accounts import AccountsRepository
    from chefmate_playground.database import Database
    accounts = AccountsRepository(Database(args.state.parent / "logical" / "accounts.db",
        url=dsn, schema=args.schema_prefix + "_accounts"))
    existing = accounts.list_users()
    if any(user["username"] != args.expected_admin and not user["username"].startswith("soak_") for user in existing):
        raise ValueError("Refusing a database containing unrelated accounts")
    admin = next((user for user in existing if user["username"] == args.expected_admin and user["role"] == "admin"), None)
    if not admin:
        raise ValueError("The dedicated soak administrator is missing")
    state = load(args.state) if args.state.is_file() else {"fixture_kind": "chefmate-disposable-multiuser",
        "created_at": now(), "base_url": args.base_url, "schema_prefix": args.schema_prefix, "users": []}
    by_name = {user["username"]: user for user in existing}
    known = {user["username"]: user for user in state["users"]}
    for index in range(args.users):
        username = f"soak_{index:03d}"
        if username in known:
            continue
        if username in by_name:
            raise ValueError("Existing fixture account has no matching private state record")
        password = secrets.token_urlsafe(24)
        user = accounts.register(username, password)
        user = accounts.complete_onboarding(user["id"], {"display_name": f"Soak kitchen {index}",
            "cooking_level": "beginner", "household_size": index % 5 + 1,
            "preferences": [f"fixture-{index}"], "theme": "dark" if index % 2 else "light", "use_for_ai": False})
        state["users"].append({"id": user["id"], "username": username, "password": password,
                               "token": accounts.issue_session(user["id"]), "index": index})
        save(args.state, state)
    state["admin_token"] = accounts.issue_session(admin["id"])
    save(args.state, state)
    print(json.dumps({"seeded_users": len(state["users"]), "private_state": str(args.state)}), flush=True)


def prepare_user(user, state, metrics):
    from PIL import Image
    client = Http(state["base_url"], user["token"], metrics)
    assert client.request("GET", "/api/auth/me")["user"]["id"] == user["id"]
    session = client.request("POST", "/api/sessions")
    user["session_id"] = session["id"]
    user["title"] = f"fixture/{user['index']}/initial"
    client.request("PATCH", "/api/sessions/" + session["id"], body={"title": user["title"]})
    memory = client.request("GET", "/api/memory")
    user["note"] = f"private-fixture-note-{user['index']}"
    client.request("PATCH", "/api/memory", body={"revision": memory["revision"], "user_notes": user["note"], "enabled": True})
    preferences = client.request("GET", "/api/ui-preferences")
    user["theme"] = "dark" if user["index"] % 2 else "light"
    client.request("PATCH", "/api/ui-preferences", body={"revision": preferences["revision"], "changes": {"theme": user["theme"]}})
    output = BytesIO()
    Image.new("RGB", (32, 24), (user["index"] * 3 % 256, 80, 150)).save(output, format="PNG")
    image = client.request("POST", f"/api/sessions/{session['id']}/images?name=fixture.png", raw=output.getvalue())
    user["image_id"] = image["id"]
    image_body = client.request("GET", f"/api/sessions/{session['id']}/images/{image['id']}")
    user["image_sha256"] = hashlib.sha256(image_body).hexdigest()
    user["token"] = client.token
    return user


def prepare(args):
    state, metrics = load(args.state), Metrics()
    admin = Http(state["base_url"], state["admin_token"], metrics)
    assert admin.request("GET", "/api/auth/me")["user"]["username"] == args.expected_admin
    expected = {user["id"] for user in state["users"]}
    assert expected <= {user["id"] for user in admin.request("GET", "/api/admin/users")["items"]}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for offset in range(0, len(state["users"]), args.concurrency):
            started = time.monotonic()
            list(pool.map(lambda user: prepare_user(user, state, metrics), state["users"][offset:offset + args.concurrency]))
            save(args.state, state)
            time.sleep(max(0, args.interval - (time.monotonic() - started)))
    state["prepared_at"] = now()
    save(args.state, state)
    print(json.dumps({"prepared_users": len(state["users"]), **metrics.snapshot()}), flush=True)


def check_user(user, peer, state, metrics, iteration, mutate=False, relogin=False):
    client = Http(state["base_url"], user["token"], metrics)
    if relogin:
        result = client.request("POST", "/api/auth/login", body={"username": user["username"], "password": user["password"]})
        assert result["user"]["id"] == user["id"]
        user["token"] = client.token
    uri = "/api/sessions/" + user["session_id"]
    assert client.request("GET", uri)["title"] == user["title"]
    memory = client.request("GET", "/api/memory")
    assert memory["user_notes"] == user["note"]
    preferences = client.request("GET", "/api/ui-preferences")
    assert preferences["values"]["theme"] == user["theme"]
    own_image = client.request("GET", uri + "/images/" + user["image_id"])
    assert hashlib.sha256(own_image).hexdigest() == user["image_sha256"]
    client.request("GET", "/api/sessions/" + peer["session_id"], expect=404,
                   headers={"X-ChefMate-User": peer["id"]})
    client.request("GET", f"/api/sessions/{peer['session_id']}/images/{peer['image_id']}", expect=404)
    client.request("GET", "/api/admin/users", expect=403)
    if mutate:
        note = f"private-fixture-{user['index']}-iteration-{iteration}"
        client.request("PATCH", "/api/memory", body={"revision": memory["revision"], "user_notes": note})
        client.request("PATCH", "/api/memory", body={"revision": memory["revision"], "user_notes": "stale-must-not-win"}, expect=409)
        user["note"] = note
        assert client.request("GET", "/api/memory")["user_notes"] == note
        theme = "dark" if user["theme"] == "light" else "light"
        client.request("PATCH", "/api/ui-preferences", body={"revision": preferences["revision"], "changes": {"theme": theme}})
        client.request("PATCH", "/api/ui-preferences", body={"revision": preferences["revision"], "changes": {"theme": user["theme"]}}, expect=409)
        user["theme"] = theme
    return user["index"]


def postgres_metrics():
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not dsn:
        return None
    import psycopg
    with psycopg.connect(dsn) as connection:
        row = connection.execute("SELECT COUNT(*),COUNT(*) FILTER(WHERE state='idle in transaction'),"
            "COUNT(*) FILTER(WHERE state='active') FROM pg_stat_activity WHERE datname=current_database()").fetchone()
        return {"connections": row[0], "idle_in_transaction": row[1], "active_including_observer": row[2]}


def run(args, *, once=False):
    state, metrics = load(args.state), Metrics()
    if not state.get("prepared_at"):
        raise ValueError("Prepare fixture sessions, images and preferences first")
    admin = Http(state["base_url"], state["admin_token"], metrics)
    assert admin.request("GET", "/api/admin/usage")["summary"]["turns"] == 0, "Fixture project unexpectedly has model usage"
    until = datetime.now(UTC) if once else datetime.fromisoformat(args.until)
    if until.tzinfo is None:
        raise ValueError("--until must have a UTC offset")
    started, iteration, errors, cursor = time.monotonic(), 0, [], 0
    metrics_path = args.state.parent / ("verify.jsonl" if once else f"{args.phase}-metrics.jsonl")
    users, last_log, last_login = state["users"], 0.0, -3600.0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while once and cursor < len(users) or not once and datetime.now(UTC) < until:
            if (args.state.parent / "stop-requested").exists():
                break
            group_started = time.monotonic()
            indices = list(range(cursor, min(cursor + args.concurrency, len(users))))
            login_group = not once and group_started - last_login >= 3600
            jobs = {index: pool.submit(check_user, users[index], users[(index + 1) % len(users)], state, metrics,
                iteration, mutate=not once and iteration % 7 == 0, relogin=once and index < 4 or login_group)
                for index in indices}
            for index, job in jobs.items():
                try:
                    job.result()
                except (AssertionError, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                    errors.append({"at": now(), "user_index": index, "type": type(error).__name__, "message": str(error)[:240]})
            if login_group:
                last_login = group_started
            iteration += 1
            cursor += args.concurrency
            if not once and cursor >= len(users):
                cursor = 0
            if time.monotonic() - last_log >= 45 or once and cursor >= len(users) or errors:
                snapshot = {"at": now(), "phase": args.phase, "elapsed_seconds": round(time.monotonic() - started, 2),
                    "groups": iteration, "users": len(users), "errors": len(errors), **metrics.snapshot(),
                    "postgres": postgres_metrics()}
                with metrics_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                save(args.state, state)
                last_log = time.monotonic()
                if errors:
                    save(args.state.parent / "failures.json", errors)
                    print(json.dumps({"verification_failed": errors[-1], **snapshot}), flush=True)
                    break
            if not once:
                time.sleep(max(0, args.interval - (time.monotonic() - group_started)))
    no_model_usage = admin.request("GET", "/api/admin/usage")["summary"]["turns"] == 0
    if not no_model_usage:
        errors.append({"at": now(), "type": "AssertionError", "message": "Fixture project unexpectedly has model usage"})
    summary = {"completed_at": now(), "mode": "verify" if once else "soak", "phase": args.phase, "users": len(users),
        "groups": iteration, "elapsed_seconds": round(time.monotonic() - started, 2),
        "errors": len(errors), **metrics.snapshot(), "postgres": postgres_metrics(), "no_model_usage": no_model_usage,
        "notice": "No model requests; disposable local single-process app, bounded concurrency; not a scalability benchmark."}
    save(args.state.parent / ("verify-summary.json" if once else f"{args.phase}-summary.json"), summary)
    save(args.state, state)
    print(json.dumps(summary), flush=True)
    if errors:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "prepare", "run", "verify"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18871")
    parser.add_argument("--expected-admin", default="soak_admin")
    parser.add_argument("--schema-prefix", default="chefmate")
    parser.add_argument("--users", type=int, default=72)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--interval", type=float, default=4)
    parser.add_argument("--until", default=None)
    parser.add_argument("--phase", default="baseline")
    parser.add_argument("--confirm-disposable", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 4 or not 65 <= args.users <= 100 or args.interval < 3:
        parser.error("Use 65..100 disposable users, 1..4 concurrent workers and interval >=3 seconds")
    if args.action == "run" and not args.until:
        parser.error("run requires --until with an explicit UTC offset")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", args.phase):
        parser.error("phase must be a short lowercase identifier")
    if args.action == "seed":
        seed(args)
    elif args.action == "prepare":
        prepare(args)
    else:
        run(args, once=args.action == "verify")


if __name__ == "__main__":
    main()
