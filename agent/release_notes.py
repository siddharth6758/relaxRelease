import os
import json
import urllib.request
import urllib.error


GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)


def generate_release_notes(commits: list[str], version: str, repo_name: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set.")

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

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}]
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_API_URL}?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode("utf-8"))
        return result["candidates"][0]["content"]["parts"][0]["text"]


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