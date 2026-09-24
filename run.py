"""Convenience launcher:  python run.py

Equivalent to:  uvicorn app.main:app --reload
"""
import uvicorn

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug_enabled,
    )


if __name__ == "__main__":
    main()
