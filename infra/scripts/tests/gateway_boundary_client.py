import json
import time
import urllib.error
import urllib.request

import jwt

base = "http://gateway:8000"
ordinary = {"X-User-ID": "spoofed-user", "X-Tenant-ID": "spoofed-tenant"}
context = {"X-CTX-User-ID": "spoofed-user", "X-CTX-Tenant-ID": "spoofed-tenant"}


def request(path, headers=None, method="GET"):
    req = urllib.request.Request(base + path, headers=headers or {}, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read()
        return response.status, dict(response.headers), json.loads(body) if body else {}


deadline = time.monotonic() + 30
while True:
    try:
        status, _, _ = request("/health/")
        if status == 200:
            break
    except (OSError, ValueError):
        pass
    if time.monotonic() >= deadline:
        raise AssertionError("isolated Kong/ASGI startup timed out")
    time.sleep(0.25)

for family in (ordinary, context):
    assert request("/api/v1/dashboard/", family)[0] == 401
    status, _, body = request("/health/", family)
    assert status == 200 and body["sanitized"] and body["tenant"] is None and body["actor"] is None
assert request("/api/v1/dashboard/", {**ordinary, **context, "Authorization": "Bearer invalid"})[0] == 401
actor = "00000000-0000-4000-8000-000000000001"
tenant = "00000000-0000-4000-8000-000000000002"
token = jwt.encode(
    {"user_id": actor, "tenant_id": tenant, "type": "access", "exp": int(time.time()) + 120},
    "gateway-runtime-test-signing-key-32-bytes",
    algorithm="HS256",
)
status, headers, body = request(
    "/api/v1/dashboard/", {**ordinary, **context, "Authorization": "Bearer " + token, "Origin": "http://localhost:8080"}
)
assert status == 200 and body == {"sanitized": True, "tenant": tenant, "actor": actor}
assert headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"
status, _, body = request("/health/", {**ordinary, **context, "Authorization": "Bearer invalid"})
assert status == 200 and body == {"sanitized": True, "tenant": None, "actor": None}
status, _, body = request("/api/v1/public/objects/", {**ordinary, **context, "Authorization": "Bearer invalid"})
assert status == 200 and body == {"sanitized": True, "tenant": tenant, "actor": None}
status, headers, _ = request(
    "/api/v1/dashboard/",
    {
        "Origin": "http://localhost:8080",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    },
    method="OPTIONS",
)
assert status == 200 and headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"
print("KONG_RUNTIME_CASES=6 PASS (real JWTAuth; users Protocol double, no DB proof)")
