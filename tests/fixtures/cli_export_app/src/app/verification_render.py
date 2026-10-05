def render_verification_markdown(analysis: dict) -> str:
    """Render a Stage B verification report as a Markdown summary."""
    lines = [f"# Verification for {analysis['repo']}"]
    lines.extend(f"- {item}" for item in analysis["findings"])
    return "\n".join(lines)
