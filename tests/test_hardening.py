def test_security_headers_present(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers.get("permissions-policy", "")


def test_health_and_ready_endpoints(client):
    assert client.get("/api/v1/health").status_code == 200
    # /ready uses the real engine; in CI it may report unavailable, but the
    # endpoint must still respond (not crash).
    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ready", "not_ready"}


def test_unknown_route_returns_json_error(client):
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body
    assert body["error"]["code"] == "NOT_FOUND"


def test_validation_error_envelope(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": 123},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_validation_error_with_valueerror_ctx_is_serializable(client, users):
    """A field_validator that raises ValueError must still yield a clean 422.

    Pydantic v2 embeds the raised exception in error ``ctx``; the custom
    handler must flatten it to a string rather than crash on serialization.
    """
    from conftest import make_auth_header

    resp = client.post(
        "/api/v1/classes",
        json={"name": "   "},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["detail"], list)
    ctx_error = body["detail"][0].get("ctx", {}).get("error")
    assert isinstance(ctx_error, str) or ctx_error is None


def test_validation_error_logging_does_not_leak_submitted_input(client, caplog):
    """PART 15/privacy: validation-error logs must not contain submitted values.

    Pydantic v2 embeds the raw ``input`` (e.g. a whole login body) in the
    error dict. If a pilot user typo'd a field, a password or full phone number
    must never be written to application logs.
    """
    import logging

    secret_password = "SuperSecretPilotPassword!42"
    with caplog.at_level(logging.WARNING, logger="app.errors"):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": 123, "password": secret_password},
        )
    assert resp.status_code == 422
    combined_logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret_password not in combined_logs
    assert "123" not in combined_logs


def test_unauthenticated_requires_bearer(client):
    resp = client.get("/api/v1/messages/batches")
    assert resp.status_code == 403 or resp.status_code == 401
