# items-api

A small Flask service for tracking items and quantities.

## Endpoints

- `GET /health` — liveness probe
- `GET /items` — list all items
- `GET /items/<id>` — fetch one item
- `POST /items` — create an item, returns 201 with the created record

## Layout

- `app/__init__.py` — application factory
- `app/routes/` — blueprints
- `app/store.py` — in-memory storage
- `app/schemas.py` — payload validation

## Tests

    pytest -q
