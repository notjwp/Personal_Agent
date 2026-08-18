"""Item endpoints.

Read routes are registered below. The create route has not been wired up.
"""
from flask import Blueprint, current_app, jsonify

from app.schemas import to_json

bp = Blueprint("items", __name__)


@bp.get("/items")
def list_items():
    store = current_app.config["STORE"]
    return jsonify([to_json(i) for i in store.list()])


@bp.get("/items/<int:item_id>")
def get_item(item_id: int):
    store = current_app.config["STORE"]
    item = store.get(item_id)
    if item is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(to_json(item))
