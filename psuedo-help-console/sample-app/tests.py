"""Tests for the sample app."""
import pytest
import sys
from pathlib import Path
from fastapi.testclient import TestClient

# Ensure sample-app directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import app


client = TestClient(app)


def test_list_users():
    """Test GET /users endpoint."""
    response = client.get("/users")
    assert response.status_code == 200
    assert "users" in response.json()
