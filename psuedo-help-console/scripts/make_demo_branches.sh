#!/bin/bash
# Create demo branches in sample-app for testing the pipeline
# Usage: cd sample-app && bash ../scripts/make_demo_branches.sh

set -e

echo "Creating demo branches for sample-app..."

# Initialize git if needed
if [ ! -d ".git" ]; then
    git init
    git config user.email "demo@example.com"
    git config user.name "Demo User"
fi

# Ensure test file exists
cat > tests.py << 'EOF'
"""Tests for the sample app."""
import pytest
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import app

client = TestClient(app)

def test_list_users():
    """Test GET /users endpoint."""
    response = client.get("/users")
    assert response.status_code == 200
    assert "users" in response.json()
EOF

# Ensure GitHub Actions workflow exists
mkdir -p .github/workflows
cat > .github/workflows/ci.yml << 'EOF'
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test-and-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install fastapi uvicorn pytest

      - name: Run tests
        run: pytest tests.py -v

      - name: Run Semgrep
        run: |
          pip install semgrep
          semgrep --config=auto . || true
EOF

# Create main branch with clean code
git checkout -b main 2>/dev/null || git checkout main

cat > app.py << 'EOF'
from fastapi import FastAPI
import sqlite3

app = FastAPI()

@app.get("/users")
def list_users():
    """List all users."""
    return {"users": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
EOF

git add app.py tests.py .github/
git commit -m "Initial commit: basic app with tests and CI" 2>/dev/null || true

# Create feature/user-search branch with planted vulnerabilities
git checkout -b feature/user-search 2>/dev/null || git checkout feature/user-search

cat > app.py << 'EOF'
from fastapi import FastAPI
import sqlite3

app = FastAPI()

@app.get("/users")
def list_users():
    """List all users."""
    return {"users": []}

@app.get("/users/search")
def search_users(query: str):
    """Search users by query - VULNERABLE to SQL injection"""
    API_KEY = "sk-1234567890abcdef"  # HARDCODED SECRET

    # SQL injection vulnerability: user input directly in query
    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"

    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(sql)
    results = cursor.fetchall()
    conn.close()

    return {
        "results": results,
        "api_key": API_KEY
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
EOF

git add app.py
git commit -m "feat: Add user search endpoint" 2>/dev/null || true

# Create fix/user-search branch (or push a follow-up commit to feature/user-search)
git checkout -b fix/user-search 2>/dev/null || git checkout fix/user-search

cat > app.py << 'EOF'
from fastapi import FastAPI
import sqlite3
import os

app = FastAPI()

@app.get("/users")
def list_users():
    """List all users."""
    return {"users": []}

@app.get("/users/search")
def search_users(query: str):
    """Search users by query - SECURE version"""
    # API key from environment variable
    API_KEY = os.getenv("API_KEY", "")

    # Use parameterized query to prevent SQL injection
    sql = "SELECT * FROM users WHERE name LIKE ?"

    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(sql, (f"%{query}%",))
    results = cursor.fetchall()
    conn.close()

    return {
        "results": results
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
EOF

git add app.py
git commit -m "fix: Secure search endpoint with parameterized queries" 2>/dev/null || true

echo "✓ Demo branches created successfully:"
echo "  - main (clean base code + tests + CI)"
echo "  - feature/user-search (planted SQL injection + hardcoded secret)"
echo "  - fix/user-search (follow-up commit with parameterized queries & env var)"
echo ""
echo "Next steps for LIVE mode demo:"
echo "  1. Add remote: git remote add origin https://github.com/YOUR_USER/sample-app.git"
echo "  2. Push branches: git push -u origin main feature/user-search"
echo "  3. Open a Pull Request from feature/user-search -> main"
echo "  4. Push fix commit: git checkout feature/user-search && git cherry-pick fix/user-search && git push"
