"""Offline defaults loaded before collecting either application test suite."""

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["OPENROUTER_API_KEY"] = "fixture-not-a-real-key"
os.environ["ENVIRONMENT"] = "ci"
os.environ["FIRESTORE_DATABASE_ID"] = "app-dev"
os.environ["ADMIN_PRIVATE_DATABASE_ID"] = "admin-private-dev"
os.environ["GOOGLE_CLOUD_PROJECT"] = "demo-rag-harass-bot"


import pytest

from shared.tests.fixtures.workbooks import write_scenario_workbook


@pytest.fixture
def synthetic_workbooks(tmp_path):
    # Keep the legacy profile sizes to exercise first/middle/last selection and
    # large exports. All cells are generated; no source workbook is copied.
    paths = (tmp_path / "chatgpt_scene.xlsx", tmp_path / "gemini_scene.xlsx")
    write_scenario_workbook(paths[0], "Sheet1", 335, label="性工法")
    write_scenario_workbook(paths[1], "工作表1", 299, label="性騷法")
    return paths
