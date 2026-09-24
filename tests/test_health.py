def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_detailed_ok(client):
    r = client.get("/health/detailed")
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "ok"
    assert body["components"]["mongodb"]["status"] == "ok"
    assert body["components"]["x_auth"]["client_id_configured"] is True
    assert body["components"]["x_auth"]["client_secret_configured"] is True


def test_health_detailed_never_leaks_secrets(client):
    text = client.get("/health/detailed").text
    assert "test-client-secret" not in text


def test_health_detailed_reports_mongo_down(client, mongo):
    mongo.healthy = False
    r = client.get("/health/detailed")
    assert r.status_code == 503
    assert r.json()["status"] == "unhealthy"
    assert r.json()["components"]["mongodb"]["status"] == "down"


def test_health_detailed_degraded_when_x_not_configured(client, x_auth):
    x_auth.problems = ["X_CLIENT_ID is not set"]
    r = client.get("/health/detailed")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["components"]["x_auth"]["status"] == "misconfigured"
