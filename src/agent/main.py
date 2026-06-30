from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

import os
import sys
from classifier import classify_release
from github_client import get_commits_between_tags, create_release_draft
from release_notes import generate_release_notes
from major_release import build_major_release_body
from notifier import send_release_notification

def run_agent(repo: str, previous_tag: str, new_tag: str, github_token: str):
    print(f"\n🚀 RelaxRelease Agent starting...")
    print(f"   Repo         : {repo}")
    print(f"   Previous tag : {previous_tag}")
    print(f"   New tag      : {new_tag}")

    # Step 1: Classify the release
    release_type = classify_release(previous_tag, new_tag)
    print(f"\n📋 Release type  : {release_type.upper()}")

    # Step 2: Fetch commits between tags
    print(f"\n📦 Fetching commits between {previous_tag} and {new_tag}...")
    diff_data = get_commits_between_tags(repo, previous_tag, new_tag, github_token)
    commits = diff_data["commits"]
    files_changed = diff_data["files_changed"]

    if not commits:
        print("⚠️  No commits found between tags. Exiting.")
        sys.exit(0)

    print(f"   Found {len(commits)} commits:")
    for c in commits:
        print(f"   - {c.splitlines()[0]}")

    # Step 3: Generate release notes based on release type
    if release_type == "major":
        print(f"\n✍️  Major release detected — generating comprehensive documentation...")
        notes = build_major_release_body(
            commits=commits,
            previous_version=previous_tag,
            new_version=new_tag,
            repo_name=repo,
            files_changed=files_changed
        )
    else:
        print(f"\n✍️  Generating release notes with Gemini...")
        notes = generate_release_notes(
            commits=commits,
            version=new_tag,
            repo_name=repo,
            files_changed=files_changed
        )

    print("\n--- Generated Release Notes ---")
    print(notes)
    print("-------------------------------")

    # Step 4: Create GitHub Release draft
    print(f"\n📝 Creating GitHub Release draft...")
    release = create_release_draft(
        repo=repo,
        tag=new_tag,
        release_notes=notes,
        token=github_token,
        release_name=f"{'🚀 Major Release' if release_type == 'major' else 'Release'} {new_tag}"
    )

    draft_url = release["html_url"]
    print(f"\n✅ Draft release created successfully!")
    print(f"   Type : {release_type.upper()}")
    print(f"   View : {draft_url}")

    # Step 5: Send email notification
    recipient = os.environ.get("NOTIFY_EMAIL")
    if recipient:
        print(f"\n📧 Sending notification email...")
        send_release_notification(
            release_type=release_type,
            version=new_tag,
            repo=repo,
            draft_url=draft_url,
            recipient_email=recipient
        )
    else:
        print(f"\n⚠️  NOTIFY_EMAIL not set — skipping email notification.")

    print(f"\n   Review the draft and click 'Publish release' when ready.")


if __name__ == "__main__":
    required_vars = ["GITHUB_TOKEN", "GITHUB_REPOSITORY", "PREVIOUS_TAG", "NEW_TAG"]
    missing = [v for v in required_vars if not os.environ.get(v)]

    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    run_agent(
        repo=os.environ["GITHUB_REPOSITORY"],
        previous_tag=os.environ["PREVIOUS_TAG"],
        new_tag=os.environ["NEW_TAG"],
        github_token=os.environ["GITHUB_TOKEN"]
    )