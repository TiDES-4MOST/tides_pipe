import os
import psycopg2

def load_creds(cfg: dict) -> dict:
    # Prefer env for containers; fall back to YAML config
    env = {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT") or 5432),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD") or "",
        "name": os.getenv("DB_NAME") or os.getenv("DB_DATABASE"),
        "sslmode": os.getenv("DB_SSLMODE") or "prefer",
    }
    if env["host"] and env["name"] and env["user"] is not None:
        return env
    return (cfg or {}).get("db_creds", {}) or {}

def connect(creds: dict):
    return psycopg2.connect(
        host=creds.get("host", "localhost"),
        port=creds.get("port", 5432),
        user=creds.get("user"),
        password=creds.get("password") or "",
        dbname=creds.get("name") or creds.get("database"),
        sslmode=creds.get("sslmode", "prefer"),
    )