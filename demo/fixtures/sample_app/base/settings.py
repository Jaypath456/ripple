import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")


def get_session():
    raise NotImplementedError("wired up by the application factory")
