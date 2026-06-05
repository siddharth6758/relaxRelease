import requests
from utils import retry, handle_rate_limit

GITHUB_API_BASE = "https://api.github.com"
TIMEOUT = 30


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RelaxRelease-Agent"
    }


def get_commits_between_tags(repo: str, base_tag: str, head_tag: str, token: str) -> list[str]:
    url = f"{GITHUB_API_BASE}/repos/{repo}/compare/{base_tag}...{head_tag}"

    def call():
        response = requests.get(url, headers=_headers(token), timeout=TIMEOUT)
        if response.status_code != 200:
            handle_rate_limit(response.status_code, response.text, "GitHub")
        return [
            c["commit"]["message"].split("\n")[0]
            for c in response.json().get("commits", [])
        ]

    return retry(call)


def create_release_draft(
    repo: str,
    tag: str,
    release_notes: str,
    token: str,
    release_name: str = None
) -> dict:
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases"

    payload = {
        "tag_name": tag,
        "name": release_name or f"Release {tag}",
        "body": release_notes,
        "draft": True,
        "prerelease": False
    }

    def call():
        response = requests.post(
            url,
            headers=_headers(token),
            json=payload,
            timeout=TIMEOUT
        )
        if response.status_code != 201:
            handle_rate_limit(response.status_code, response.text, "GitHub")
        return response.json()

    return retry(call)


if __name__ == "__main__":
    print("GitHub client module loaded successfully.")
    print("Functions available:")
    print("  - get_commits_between_tags(repo, base_tag, head_tag, token)")
    print("  - create_release_draft(repo, tag, release_notes, token)")