import os
import requests
from utils import retry, handle_rate_limit

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
TIMEOUT = 60


def generate_release_notes(commits: list[str], version: str, repo_name: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY environment variable is not set.")
        import sys; sys.exit(1)

    commit_text = "\n".join(f"- {c}" for c in commits)

    prompt = f"""You are a technical writer generating release notes for a software project.

Repository: {repo_name}
New version: {version}

Commits since last release:
{commit_text}

Generate professional release notes including:
1. A short summary paragraph (2-3 sentences)
2. A categorized list of changes (Features, Bug Fixes, Improvements, Other)
3. Upgrade notes if anything looks breaking
4. Changelog entry (single line, suitable for CHANGELOG.md)

Be concise, professional, and developer-friendly.
Only include categories that have relevant commits.
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    def call():
        response = requests.post(
            f"{GEMINI_API_BASE}?key={api_key}",
            json=payload,
            timeout=TIMEOUT
        )
        if response.status_code != 200:
            handle_rate_limit(response.status_code, response.text, "Gemini")
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]

    return retry(call)


if __name__ == "__main__":
    sample_commits = [
        "feat: add user authentication with JWT tokens",
        "fix: resolve crash when input is empty string",
        "feat: add dark mode support",
        "chore: update dependencies",
        "fix: correct typo in error message",
        "feat: add export to CSV functionality",
    ]

    notes = generate_release_notes(
        commits=sample_commits,
        version="1.2.0",
        repo_name="relax-release"
    )
    print(notes)