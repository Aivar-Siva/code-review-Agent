"""
Sample module with intentional issues to test the PR review agent.
DO NOT use in production.
"""
import subprocess
import sqlite3


DB_PASSWORD = "admin123"  # hardcoded secret
API_KEY = "sk-prod-abc123xyz789"  # hardcoded API key


def get_user(user_id):
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    # SQL injection vulnerability
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    return cursor.fetchone()
    # connection never closed — resource leak


def run_command(user_input):
    # command injection
    result = subprocess.run(f"echo {user_input}", shell=True, capture_output=True)
    return result.stdout


def divide(a, b):
    # no zero division guard
    return a / b


def process_items(items):
    total = 0
    # off-by-one: should be range(len(items))
    for i in range(len(items) + 1):
        total += items[i]
    return total


def read_file(filename):
    # path traversal — no sanitization
    with open(f"/var/data/{filename}") as f:
        return f.read()
