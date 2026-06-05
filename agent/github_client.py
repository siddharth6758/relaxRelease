import json
import urllib.request
import urllib.error


GITHUB_API_BASE = "https://api.github.com"


def get_commits_between_tags(repo: str, base_tag: str, head_tag: str, token: str) -> list[str]:
    """
    Fetches commit messages between two tags.
    repo format: "owner/repo-name"
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/compare/{base_tag}...{head_tag}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RelaxRelease-Agent"
        }
    )

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
            commits = [
                c["commit"]["message"].split("\n")[0]
                for c in data.get("commits", [])
            ]
            return commits
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"GitHub API error {e.code}: {body}")


def create_release_draft(
    repo: str,
    tag: str,
    release_notes: str,
    token: str,
    release_name: str = None
) -> dict:
    """
    Creates a draft release on GitHub with the given release notes.
    Returns the created release data including the URL.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases"

    payload = json.dumps({
        "tag_name": tag,
        "name": release_name or f"Release {tag}",
        "body": release_notes,
        "draft": True,
        "prerelease": False
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RelaxRelease-Agent",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"GitHub API error {e.code}: {body}")


if __name__ == "__main__":
    print("GitHub client module loaded successfully.")
    print("Functions available:")
    print("  - get_commits_between_tags(repo, base_tag, head_tag, token)")
    print("  - create_release_draft(repo, tag, release_notes, token)")