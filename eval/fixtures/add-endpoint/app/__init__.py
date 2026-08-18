"""Application factory."""
from flask import Flask

from app.routes import health, items
from app.store import ItemStore


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["STORE"] = ItemStore()
    app.register_blueprint(health.bp)
    app.register_blueprint(items.bp)
    return app
