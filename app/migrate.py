from app.config import Settings
from app.db import Database
from pathlib import Path


def main() -> None:
    settings = Settings.from_env()
    settings.validate()
    Database(settings.database_target).initialize()
    if settings.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver
        connection_string = settings.database_url.replace(
            "postgresql+psycopg://", "postgresql://"
        )
        with PostgresSaver.from_conn_string(connection_string) as saver:
            saver.setup()
        rls = Path(__file__).with_name("migrations") / "002_langgraph_rls.sql"
        with Database(settings.database_target).connect() as connection:
            connection.execute(rls.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
