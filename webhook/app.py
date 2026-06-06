import os
import hmac
import hashlib
import subprocess
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

app = Flask(__name__)


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verifies that the webhook request genuinely came from GitHub.
    GitHub signs every webhook with a secret you define.
    """
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint — Koyeb uses this to verify the service is running."""
    return jsonify({"status": "ok", "service": "RelaxRelease Webhook Receiver"}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Receives GitHub webhook events.
    Filters for tag push events and triggers the release agent.
    """
    # 1. Verify signature
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_github_signature(request.data, signature, secret):
            return jsonify({"error": "Invalid signature"}), 401

    # 2. Only handle push events
    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return jsonify({"message": f"Ignoring event: {event}"}), 200

    payload = request.json
    if not payload:
        return jsonify({"error": "No payload"}), 400

    # 3. Only handle tag pushes (refs/tags/v*)
    ref = payload.get("ref", "")
    if not ref.startswith("refs/tags/"):
        return jsonify({"message": "Not a tag push — ignoring"}), 200

    tag = ref.replace("refs/tags/", "")
    repo = payload.get("repository", {}).get("full_name", "")

    print(f"✅ Tag push received: {tag} on {repo}")

    return jsonify({
        "message": "Webhook received",
        "tag": tag,
        "repo": repo,
        "status": "queued"
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "RelaxRelease",
        "status": "running",
        "endpoints": ["/health", "/webhook"]
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)