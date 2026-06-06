import os
import json
import requests
from src.agent.utils import retry, handle_rate_limit

RESEND_API_URL = "https://api.resend.com/emails"
TIMEOUT = 30


def send_release_notification(
    release_type: str,
    version: str,
    repo: str,
    draft_url: str,
    recipient_email: str
) -> bool:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("⚠️  RESEND_API_KEY not set — skipping email notification.")
        return False

    type_emoji = "🚀" if release_type == "major" else "📦"
    type_label = "Major Release" if release_type == "major" else "Minor Release"

    subject = f"{type_emoji} {type_label} Draft Ready — {repo} {version}"

    html_body = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>{type_emoji} Release Draft Ready for Review</h2>
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 8px; font-weight: bold;">Repository</td>
                <td style="padding: 8px;">{repo}</td>
            </tr>
            <tr style="background: #f5f5f5;">
                <td style="padding: 8px; font-weight: bold;">Version</td>
                <td style="padding: 8px;">{version}</td>
            </tr>
            <tr>
                <td style="padding: 8px; font-weight: bold;">Release Type</td>
                <td style="padding: 8px;">{type_label}</td>
            </tr>
        </table>
        <br/>
        <a href="{draft_url}"
           style="background: #2da44e; color: white; padding: 12px 24px;
                  text-decoration: none; border-radius: 6px; display: inline-block;">
            Review Draft Release
        </a>
        <br/><br/>
        <p style="color: #666; font-size: 12px;">
            This draft has NOT been published. Review and click
            "Publish release" on GitHub when ready.
        </p>
        <p style="color: #666; font-size: 12px;">
            Sent by RelaxRelease Agent
        </p>
    </div>
    """

    text_body = f"""
{type_emoji} Release Draft Ready for Review

Repository : {repo}
Version    : {version}
Type       : {type_label}

Review the draft here:
{draft_url}

This draft has NOT been published.
Review and click "Publish release" on GitHub when ready.

Sent by RelaxRelease Agent
    """

    payload = {
        "from": "RelaxRelease <onboarding@resend.dev>",
        "to": [recipient_email],
        "subject": subject,
        "html": html_body,
        "text": text_body
    }

    def call():
        response = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=TIMEOUT
        )
        if response.status_code not in (200, 201):
            handle_rate_limit(response.status_code, response.text, "Resend")
        return True

    result = retry(call)
    print(f"📧 Notification email sent to {recipient_email}")
    return result


if __name__ == "__main__":
    print("Notifier module loaded successfully.")
    print("Function available:")
    print("  - send_release_notification(release_type, version, repo, draft_url, recipient_email)")