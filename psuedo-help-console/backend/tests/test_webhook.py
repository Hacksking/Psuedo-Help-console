"""Test webhook signature verification and diff line parser."""
import hmac
import hashlib
import pytest
from backend.app.github_client import verify_webhook_signature, parse_diff_lines


def test_webhook_signature_valid():
    """Verify a valid webhook signature."""
    secret = "test-secret"
    payload = b'{"action": "opened"}'
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, sig, secret)


def test_webhook_signature_invalid():
    """Reject an invalid webhook signature."""
    secret = "test-secret"
    payload = b'{"action": "opened"}'
    bad_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"

    assert not verify_webhook_signature(payload, bad_sig, secret)


def test_webhook_signature_empty_or_malformed():
    """Reject empty, malformed, or missing prefix signatures."""
    secret = "test-secret"
    payload = b'{"action": "opened"}'
    assert not verify_webhook_signature(payload, "", secret)
    assert not verify_webhook_signature(payload, "invalid_prefix", secret)
    assert not verify_webhook_signature(payload, "sha256=123", "")


def test_parse_diff_lines():
    """Ensure diff line parser correctly identifies line numbers on right side."""
    diff = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 import os
+import sqlite3
+API_KEY = "xyz"
 print("ok")
"""
    parsed = parse_diff_lines(diff)
    assert "app.py" in parsed
    # Line 2 and Line 3 were added (+)
    assert 2 in parsed["app.py"]
    assert 3 in parsed["app.py"]
