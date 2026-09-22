from __future__ import annotations

from app.main import app


def main() -> None:
    app.state.worker.run_forever()


if __name__ == "__main__":
    main()
