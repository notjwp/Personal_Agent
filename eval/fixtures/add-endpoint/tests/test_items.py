def test_list_items_is_empty_initially(client):
    resp = client.get("/items")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_get_missing_item_returns_404(client):
    resp = client.get("/items/42")
    assert resp.status_code == 404


def test_create_item(client):
    resp = client.post("/items", json={"name": "nails", "quantity": 10})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"] == 1
    assert body["name"] == "nails"
    assert body["quantity"] == 10


def test_created_item_is_then_listed(client):
    client.post("/items", json={"name": "nails", "quantity": 10})
    resp = client.get("/items")
    assert resp.status_code == 200
    assert [i["name"] for i in resp.get_json()] == ["nails"]


def test_create_item_rejects_invalid_payload(client):
    resp = client.post("/items", json={"name": ""})
    assert resp.status_code == 400
