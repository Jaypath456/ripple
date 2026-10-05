from app.verification import verify_repository
from app.verification_render import render_verification_markdown


def test_verification_markdown_summary():
    assert render_verification_markdown(verify_repository("repo")).startswith("#")
