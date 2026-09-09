from conftest import make_auth_header


def _payload(name="2026-2027", start="2026-01-01", end="2026-12-31", is_active=True):
    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "is_active": is_active,
    }


def test_create_and_get_academic_year(client, users):
    resp = client.post(
        "/api/v1/academic-years",
        json=_payload(),
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "2026-2027"
    assert body["start_date"] == "2026-01-01"
    assert body["end_date"] == "2026-12-31"
    assert body["is_active"] is True

    got = client.get(
        f"/api/v1/academic-years/{body['id']}",
        headers=make_auth_header(users["admin"]),
    )
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]

    listed = client.get(
        "/api/v1/academic-years", headers=make_auth_header(users["admin"])
    )
    assert listed.status_code == 200
    assert any(y["name"] == "2026-2027" for y in listed.json())


def test_academic_year_active_overlap_rejected(client, users):
    headers = make_auth_header(users["admin"])
    assert (
        client.post("/api/v1/academic-years", json=_payload(), headers=headers).status_code
        == 201
    )
    resp = client.post(
        "/api/v1/academic-years",
        json=_payload(name="2026-2027B", start="2026-06-01", end="2027-05-31"),
        headers=headers,
    )
    assert resp.status_code == 400
    assert "overlaps" in resp.json()["detail"].lower()


def test_academic_year_inactive_overlap_allowed(client, users):
    headers = make_auth_header(users["admin"])
    assert (
        client.post("/api/v1/academic-years", json=_payload(), headers=headers).status_code
        == 201
    )
    resp = client.post(
        "/api/v1/academic-years",
        json=_payload(
            name="2025-2026", start="2025-06-01", end="2026-05-31", is_active=False
        ),
        headers=headers,
    )
    assert resp.status_code == 201


def test_academic_year_duplicate_name_conflict(client, users):
    headers = make_auth_header(users["admin"])
    assert (
        client.post("/api/v1/academic-years", json=_payload(), headers=headers).status_code
        == 201
    )
    resp = client.post(
        "/api/v1/academic-years",
        json=_payload(name="2026-2027", start="2030-01-01", end="2030-12-31"),
        headers=headers,
    )
    assert resp.status_code == 409


def test_academic_year_bad_range_rejected(client, users):
    resp = client.post(
        "/api/v1/academic-years",
        json=_payload(start="2026-12-31", end="2026-01-01"),
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400
    assert "must not be after" in resp.json()["detail"]


def test_academic_year_update_active_overlap_rejected(client, db_session, users):
    headers = make_auth_header(users["admin"])
    first = client.post(
        "/api/v1/academic-years", json=_payload(), headers=headers
    ).json()
    second = client.post(
        "/api/v1/academic-years",
        json=_payload(
            name="2025-2026", start="2025-01-01", end="2025-12-31", is_active=False
        ),
        headers=headers,
    ).json()

    resp = client.put(
        f"/api/v1/academic-years/{second['id']}",
        json={"is_active": True, "end_date": "2026-06-30"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "overlaps" in resp.json()["detail"].lower()

    ok = client.put(
        f"/api/v1/academic-years/{first['id']}",
        json={"name": "2026-2027 Renamed"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "2026-2027 Renamed"


def test_academic_year_teacher_read_only(client, users):
    listed = client.get(
        "/api/v1/academic-years", headers=make_auth_header(users["teacher"])
    )
    assert listed.status_code == 200

    resp = client.post(
        "/api/v1/academic-years",
        json=_payload(),
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 403