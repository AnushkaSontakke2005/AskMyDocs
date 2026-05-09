from datetime import datetime
from os import getenv
from threading import Thread
from urllib.parse import urlparse

from fastapi import FastAPI


app = FastAPI(
    title="AskMyDocs API",
    description="Backend API for the AskMyDocs document intelligence system.",
    version="0.1.0",
)


@app.get("/health", tags=["System"])
def health_check():
    return {
        "status": "ok",
        "service": "askmydocs-api",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/debug/database", tags=["System"])
def database_debug():
    from backend.db import db

    database_url = getenv("DATABASE_URL", "")
    parsed_database_url = urlparse(database_url) if database_url else None

    return {
        "database_class": type(db).__name__,
        "is_closed": db.is_closed(),
        "database_name": db.database,
        "has_database_url": bool(getenv("DATABASE_URL")),
        "database_url_looks_valid": "://" in getenv("DATABASE_URL", ""),
        "database_url_has_name": bool(parsed_database_url and parsed_database_url.path.strip("/")),
        "has_pg_vars": all(
            getenv(name)
            for name in ["PGDATABASE", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD"]
        ),
        "has_postgres_vars": all(
            getenv(name)
            for name in [
                "POSTGRES_DB_NAME",
                "POSTGRES_DB_HOST",
                "POSTGRES_DB_PORT",
                "POSTGRES_DB_USER",
                "POSTGRES_DB_PASSWORD",
            ]
        ),
        "has_standard_postgres_vars": all(
            getenv(name)
            for name in [
                "POSTGRES_DB",
                "POSTGRES_HOST",
                "POSTGRES_PORT",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
            ]
        ),
    }


@app.on_event("startup")
def start_database_initialization():
    from backend.db import initialize_database

    Thread(target=initialize_database, daemon=True).start()


def include_app_routers():
    from backend.routes.admin import router as admin_router
    from backend.routes.auth import router as auth_router
    from backend.routes.chat import router as chat_router
    from backend.routes.documents import router as documents_router
    from backend.routes.jobs import router as jobs_router
    from backend.routes.tags import router as tags_router

    app.include_router(auth_router)
    app.include_router(documents_router)
    app.include_router(jobs_router)
    app.include_router(chat_router)
    app.include_router(tags_router)
    app.include_router(admin_router)


include_app_routers()
