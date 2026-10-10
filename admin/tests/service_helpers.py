"""Invoke diagnostic business logic in a Flask context, without an HTTP route."""

from flask import Flask

from admin_app.api import run_chat_test


def run_diagnostic(payload):
    app = Flask(__name__)
    with app.test_request_context():
        return app.make_response(run_chat_test(payload))
