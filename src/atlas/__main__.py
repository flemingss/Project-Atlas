from __future__ import annotations

import uvicorn

from atlas.api import create_app
from atlas.logging_config import configure_logging
from atlas.settings import Settings


def main() -> None:
    settings = Settings()
    configure_logging(settings.atlas_log_level)
    app = create_app()
    uvicorn.run(app, host=settings.atlas_host, port=settings.atlas_port, log_level=settings.atlas_log_level.lower())


if __name__ == "__main__":
    main()
