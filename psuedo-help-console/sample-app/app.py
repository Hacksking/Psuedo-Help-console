"""Tiny sample FastAPI app for demo purposes."""
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
