#!/usr/bin/env python3
"""Build the Slack Block Kit payload for a bundled-image build failure.

Reads the notification fields from the environment (set by action.yml) and
prints the ``chat.postMessage`` payload as a single line of JSON to stdout.
Kept as a standalone module so the field and escaping logic is lintable and
unit-testable rather than embedded in the composite action's YAML.
"""

import json
import os

# #ds-release. The single source of the default: action.yml deliberately defaults its
# input to the empty string, because a caller that passes an explicitly empty value
# bypasses a composite input's default entirely, and the notification then goes nowhere.
DEFAULT_CHANNEL = "C0BQS6PFW14"


def slack_escape(value: str) -> str:
    """Escape the three characters Slack mrkdwn treats as control characters."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_object(raw: str) -> dict:
    """Parse a JSON object, returning an empty dict for anything else.

    Every caller here reads a value produced by an earlier job. That job can
    fail before it produces anything, so unparseable input must degrade to
    "no information" rather than raise. A notification that crashes tells
    nobody that the build broke.
    """
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def plugin_identity(raw: str) -> tuple[str, str]:
    """Read the plugin id and version out of the build job's plugin JSON."""
    plugin = _load_object(raw)
    return str(plugin.get("id", "")), str(plugin.get("version", ""))


def failed_stages(raw: str) -> list[str]:
    """Names of the jobs that did not succeed, in the order given.

    Accepts either a flat name-to-result mapping or GitHub's own ``needs``
    shape, where each entry is an object holding ``result`` and ``outputs``.
    Supporting both means a caller can pass ``toJSON(needs)`` straight through
    instead of hand-writing a JSON object per job and keeping it in step with
    the job list.

    A skipped job is not a failure. The rollout step is skipped on every run
    that does not ask for it, and reporting that as a failure would point the
    reader at the wrong stage.
    """
    stages = []
    for name, value in _load_object(raw).items():
        result = value.get("result", "") if isinstance(value, dict) else value
        if result not in ("success", "skipped"):
            stages.append(name)
    return stages


def build_payload(env: dict[str, str]) -> dict:
    """Build the chat.postMessage payload from the notification environment."""
    repo = slack_escape(env["REPO"])
    ref_name = slack_escape(env.get("REF_NAME", ""))
    actor = slack_escape(env.get("ACTOR", ""))
    sha = slack_escape(env.get("SHA", "")[:8])
    run_url = env["RUN_URL"]

    plugin_id, plugin_version = plugin_identity(env.get("PLUGIN_JSON", ""))
    stages = failed_stages(env.get("JOB_RESULTS", ""))

    stage_text = ", ".join(slack_escape(s) for s in stages) if stages else "unknown"
    fallback = f"Bundled image build failed for {repo} at {stage_text}"

    fields = [
        {"type": "mrkdwn", "text": f"*Repository:*\n{repo}"},
        {"type": "mrkdwn", "text": f"*Failed at:*\n{stage_text}"},
        {"type": "mrkdwn", "text": f"*Branch:*\n{ref_name}"},
        {"type": "mrkdwn", "text": f"*Triggered by:*\n{actor}"},
    ]
    # Optional fields are only shown when the value exists, so a job that failed
    # before producing one never renders an empty-valued row.
    if plugin_id:
        plugin_text = f"{plugin_id} {plugin_version}".strip()
        fields.append({"type": "mrkdwn", "text": f"*Plugin:*\n{slack_escape(plugin_text)}"})
    for label, key in (("Image", "IMAGE"), ("Grafana base image", "GRAFANA_IMAGE")):
        if env.get(key):
            fields.append({"type": "mrkdwn", "text": f"*{label}:*\n`{slack_escape(env[key])}`"})
    fields.append({"type": "mrkdwn", "text": f"*Commit:*\n`{sha}`"})

    return {
        "channel": env.get("SLACK_CHANNEL_ID") or DEFAULT_CHANNEL,
        "text": fallback,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f":x: *Bundled image build failed*\n{fallback}"}},
            {"type": "section", "fields": fields},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"<{run_url}|View the failed run>"}},
        ],
    }


if __name__ == "__main__":
    print(json.dumps(build_payload(dict(os.environ))))
