#!/usr/bin/env python3
"""Build the Slack Block Kit payload for a Cloud E2E failure notification.

Reads the notification fields from the environment (set by action.yml) and
prints the ``chat.postMessage`` payload as a single line of JSON to stdout.
Kept as a standalone module so the field/escaping logic is lintable and
unit-testable rather than embedded in the composite action's YAML.
"""

import json
import os


def slack_escape(value: str) -> str:
    """Escape the three characters Slack mrkdwn treats as control characters."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_payload(env: dict[str, str]) -> dict:
    """Build the chat.postMessage payload from the notification environment."""
    repo = slack_escape(env["REPO"])
    run_stage = slack_escape(env.get("RUN_STAGE", ""))
    ref_name = slack_escape(env.get("REF_NAME", ""))
    actor = slack_escape(env.get("ACTOR", ""))
    sha = slack_escape(env.get("SHA", "")[:8])
    run_url = env["RUN_URL"]

    fallback = f"Cloud E2E tests failed for {repo} ({run_stage})"

    fields = [
        {"type": "mrkdwn", "text": f"*Repository:*\n{repo}"},
        {"type": "mrkdwn", "text": f"*Run stage:*\n{run_stage}"},
        {"type": "mrkdwn", "text": f"*Branch:*\n{ref_name}"},
        {"type": "mrkdwn", "text": f"*Triggered by:*\n{actor}"},
    ]
    # Optional fields are only shown when the caller provides them, so an
    # unset input never renders an empty-valued row.
    for label, key in (("Grafana Cloud URL", "GRAFANA_URL"), ("Datasource version", "DATASOURCE_VERSION")):
        if env.get(key):
            fields.append({"type": "mrkdwn", "text": f"*{label}:*\n{slack_escape(env[key])}"})
    fields.append({"type": "mrkdwn", "text": f"*Commit:*\n`{sha}`"})

    return {
        "channel": env["SLACK_CHANNEL_ID"],
        "text": fallback,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f":x: *Cloud E2E tests failed*\n{fallback}"}},
            {"type": "section", "fields": fields},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"<{run_url}|View the failed run>"}},
        ],
    }


if __name__ == "__main__":
    print(json.dumps(build_payload(dict(os.environ))))
