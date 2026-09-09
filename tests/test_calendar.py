from datetime import date

from conftest import make_auth_header


def _seed_year(db, admin_user):
    from app.models.academic_year import AcademicYear

    year = AcademicYear(
        name="2026-2027",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        is_active=True,
        created_by=admin_user.id,
    )
    db.add(year)
    db.commit()
    db.refresh(year)
    return year


def test_calendar_create_list_get_and_delete(client, db_session, users):
    headers = make_auth_header(users["admin"])
    year = _seed_year(db_session, users["admin"])

    resp = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "Independence Day",
            "event_type": "HOLIDAY",
            "holiday_type": "PUBLIC_HOLIDAY",
            "start_date": "2026-08-15",
            "end_date": "2026-08-15",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_type"] == "HOLIDAY"
    assert body["holiday_type"] == "PUBLIC_HOLIDAY"

    listed = client.get("/api/v1/calendar", headers=headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert any(e["id"] == body["id"] for e in items)

    got = client.get(f"/api/v1/calendar/{body['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["title"] == "Independence Day"

    deleted = client.delete(f"/api/v1/calendar/{body['id']}", headers=headers)
    assert deleted.status_code == 200

    missing = client.get(f"/api/v1/calendar/{body['id']}", headers=headers)
    assert missing.status_code == 404


def test_calendar_holiday_requires_type(client, db_session, users):
    headers = make_auth_header(users["admin"])
    year = _seed_year(db_session, users["admin"])
    resp = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "No Type",
            "event_type": "HOLIDAY",
            "holiday_type": None,
            "start_date": "2026-08-15",
            "end_date": "2026-08-15",
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"]


def test_calendar_non_holiday_rejects_type(client, db_session, users):
    headers = make_auth_header(users["admin"])
    year = _seed_year(db_session, users["admin"])
    resp = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "Sports Day",
            "event_type": "SCHOOL_EVENT",
            "holiday_type": "PUBLIC_HOLIDAY",
            "start_date": "2026-07-01",
            "end_date": "2026-07-01",
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "HOLIDAY" in resp.json()["detail"]


def test_calendar_overlapping_holiday_rejected(client, db_session, users):
    headers = make_auth_header(users["admin"])
    year = _seed_year(db_session, users["admin"])
    payload = {
        "academic_year_id": year.id,
        "event_type": "HOLIDAY",
        "holiday_type": "SCHOOL_HOLIDAY",
        "start_date": "2026-08-15",
        "end_date": "2026-08-20",
    }
    ok = client.post(
        "/api/v1/calendar", json={**payload, "title": "First"}, headers=headers
    )
    assert ok.status_code == 201
    overlap = client.post(
        "/api/v1/calendar",
        json={**payload, "title": "Second", "start_date": "2026-08-18"},
        headers=headers,
    )
    assert overlap.status_code == 400
    assert "overlaps" in overlap.json()["detail"]

    # Holidays in a different academic year never conflict.
    from app.models.academic_year import AcademicYear

    year2 = AcademicYear(
        name="2025-2026",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        is_active=False,
        created_by=users["admin"].id,
    )
    db_session.add(year2)
    db_session.commit()
    allowed = client.post(
        "/api/v1/calendar",
        json={
            **payload,
            "academic_year_id": year2.id,
            "title": "Older Year Holiday",
            "start_date": "2025-08-18",
            "end_date": "2025-08-18",
        },
        headers=headers,
    )
    assert allowed.status_code == 201


def test_calendar_outside_year_rejected(client, db_session, users):
    headers = make_auth_header(users["admin"])
    year = _seed_year(db_session, users["admin"])
    resp = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "Out of Range",
            "event_type": "HOLIDAY",
            "holiday_type": "OTHER",
            "start_date": "2027-03-01",
            "end_date": "2027-03-02",
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "within their academic year" in resp.json()["detail"]


def test_calendar_update_event(client, db_session, users):
    headers = make_auth_header(users["admin"])
    year = _seed_year(db_session, users["admin"])
    created = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "Old Title",
            "event_type": "HOLIDAY",
            "holiday_type": "EXAM_BREAK",
            "start_date": "2026-09-01",
            "end_date": "2026-09-05",
        },
        headers=headers,
    ).json()

    updated = client.put(
        f"/api/v1/calendar/{created['id']}",
        json={"title": "New Title", "end_date": "2026-09-06"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "New Title"
    assert updated.json()["end_date"] == "2026-09-06"


def test_calendar_teacher_read_only(client, db_session, users):
    year = _seed_year(db_session, users["admin"])
    listed = client.get(
        "/api/v1/calendar", headers=make_auth_header(users["teacher"])
    )
    assert listed.status_code == 200

    resp = client.post(
        "/api/v1/calendar",
        json={
            "academic_year_id": year.id,
            "title": "Blocked",
            "event_type": "HOLIDAY",
            "holiday_type": "OTHER",
            "start_date": "2026-09-10",
            "end_date": "2026-09-10",
        },
        headers=make_auth_header(users["teacher"]),
    )
    assert resp.status_code == 403