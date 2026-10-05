from app.cli import main


def test_verify_command():
    assert main(["verify", "repo"]) == 0
