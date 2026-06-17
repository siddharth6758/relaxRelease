import requests
from src.agent.utils import retry, handle_rate_limit

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


def create_webhook(github_token: str, repo_full_name: str, webhook_url: str, secret: str) -> dict:
    """Auto-installs webhook on user's repo via GitHub API."""
    response = requests.post(
        f"https://api.github.com/repos/{repo_full_name}/hooks",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        },
        json={
            "name": "web",
            "active": True,
            "events": ["create"],
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "secret": secret
            }
        },
        timeout=10
    )
    return response.json()


def delete_webhook(github_token: str, repo_full_name: str, webhook_id: int) -> bool:
    """Removes webhook from user's repo."""
    response = requests.delete(
        f"https://api.github.com/repos/{repo_full_name}/hooks/{webhook_id}",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        },
        timeout=10
    )
    return response.status_code == 204


if __name__ == "__main__":
    print("GitHub client module loaded successfully.")
    print("Functions available:")
    print("  - get_commits_between_tags(repo, base_tag, head_tag, token)")
    print("  - create_release_draft(repo, tag, release_notes, token)")