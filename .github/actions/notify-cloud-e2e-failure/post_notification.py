#!/usr/bin/env python3
"""Post a Cloud E2E failure notification into the day's shared Slack thread.

Every caller's nightly cron fires at the same time, so rather than one
top-level message per failing repository, failures for a UTC day are collected
as replies under a single parent message. The parent is found by its message
metadata (not its text), created by whichever run fails first, and
de-duplicated if two runs race to create it: every run converges on the
earliest parent and a run that lost the race deletes its own.

Reads the notification fields from the environment (set by action.yml), and
writes ``ts``, ``thread-ts`` and ``channel-id`` to ``$GITHUB_OUTPUT``. Kept as
a standalone module so the Slack calls are unit-testable with a fake client.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from build_payload import build_payload

# Identifies the daily parent message. Matched through message metadata so a
# human quoting the parent's text, or a reworded parent, never breaks lookup.
EVENT_TYPE = "cloud_e2e_daily_thread"

# Delay before re-listing after creating a parent, so a parent a concurrent run
# posted a moment earlier is visible to conversations.history.
RACE_SETTLE_SECONDS = 2.0


class SlackError(Exception):
    """A Slack Web API call returned ``ok: false`` or could not be made."""


class SlackClient:
    """Minimal Slack Web API client over urllib, so the action needs no dependencies."""

    def __init__(self, token: str, base_url: str = "https://slack.com/api/"):
        self._token = token
        self._base_url = base_url

    def call(self, method: str, **params) -> dict:
        # Form encoding works for both read and write methods; structured
        # arguments (blocks, metadata) are sent as JSON strings, as Slack expects.
        fields = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in params.items() if v is not None}
        request = urllib.request.Request(
            self._base_url + method,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Authorization": f"Bearer {self._token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
        except OSError as err:
            raise SlackError(f"{method}: {err}") from err
        if not body.get("ok"):
            raise SlackError(f"{method}: {body.get('error', 'unknown error')}")
        return body


def ts_key(ts: str) -> tuple[int, int]:
    """Order Slack timestamps exactly, without float rounding."""
    seconds, _, micros = ts.partition(".")
    return int(seconds), int(micros or 0)


def utc_midnight(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def find_daily_parents(client: SlackClient, channel: str, now: datetime) -> list[str]:
    """Return the ts of every parent for ``now``'s UTC day, earliest first."""
    day = utc_midnight(now)
    date = day.date().isoformat()
    found = []
    cursor = None
    while True:
        body = client.call(
            "conversations.history",
            channel=channel,
            oldest=f"{day.timestamp():.6f}",
            include_all_metadata="true",
            limit=200,
            cursor=cursor,
        )
        for message in body.get("messages", []):
            metadata = message.get("metadata") or {}
            if metadata.get("event_type") == EVENT_TYPE and (metadata.get("event_payload") or {}).get("date") == date:
                found.append(message["ts"])
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return sorted(found, key=ts_key)


def ensure_daily_parent(client: SlackClient, channel: str, now: datetime, sleep=time.sleep) -> str:
    """Return the ts of the day's parent, creating it if no run has yet."""
    existing = find_daily_parents(client, channel, now)
    if existing:
        return existing[0]

    date = utc_midnight(now).date().isoformat()
    mine = client.call(
        "chat.postMessage",
        channel=channel,
        text=f":rotating_light: Cloud E2E failures for {date} (UTC). Each failing run replies in this thread.",
        metadata={"event_type": EVENT_TYPE, "event_payload": {"date": date}},
    )["ts"]

    # Another run may have created a parent between our lookup and our post.
    # The earliest one wins; everyone else removes theirs.
    sleep(RACE_SETTLE_SECONDS)
    earliest = min([mine, *find_daily_parents(client, channel, now)], key=ts_key)
    if earliest != mine:
        try:
            client.call("chat.delete", channel=channel, ts=mine)
        except SlackError as err:
            print(f"::warning::Could not delete duplicate daily parent {mine}: {err}", file=sys.stderr)
    return earliest


def notify(client: SlackClient, env: dict[str, str], now: datetime, sleep=time.sleep) -> dict[str, str]:
    """Post the failure notification and return the action's outputs."""
    payload = build_payload(env)
    channel = payload["channel"]

    thread_ts = ""
    if env.get("DAILY_THREAD", "true").lower() == "true":
        try:
            thread_ts = ensure_daily_parent(client, channel, now, sleep)
        except SlackError as err:
            # A broken thread lookup (e.g. a missing history scope) must never
            # swallow the failure alert itself, so fall back to posting top level.
            print(f"::warning::Could not use the daily thread, posting top level instead: {err}", file=sys.stderr)

    if thread_ts:
        payload["thread_ts"] = thread_ts
    reply = client.call("chat.postMessage", **payload)
    return {"ts": reply["ts"], "thread-ts": thread_ts, "channel-id": reply.get("channel", channel)}


def main() -> None:
    env = dict(os.environ)
    outputs = notify(SlackClient(env["SLACK_BOT_TOKEN"]), env, datetime.now(timezone.utc))
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
