#!/usr/bin/env python3
"""Post a Cloud E2E failure notification, threading replies under a daily parent."""

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from build_payload import build_payload

EVENT_TYPE = "cloud_e2e_daily_thread"

RACE_SETTLE_SECONDS = 2.0

MAX_RETRIES = 5
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class SlackError(Exception):
    """A Slack Web API call returned ``ok: false`` or could not be made."""


class SlackClient:
    """Minimal Slack Web API client over urllib."""

    def __init__(self, token: str):
        self._token = token

    def call(self, method: str, **params) -> dict:
        # Slack expects blocks and metadata as JSON strings.
        fields = {
            k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v)
            for k, v in params.items()
            if v is not None
        }
        request = urllib.request.Request(
            "https://slack.com/api/" + method,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Authorization": f"Bearer {self._token}"},
        )
        body = self._request_with_retries(method, request)
        if not body.get("ok"):
            error = body.get("error", "unknown error")
            detail = ""
            if error == "missing_scope":
                detail = f" (needed={body.get('needed', '?')}, provided={body.get('provided', '?')})"
            raise SlackError(f"{method}: {error}{detail}")
        return body

    def _request_with_retries(self, method: str, request: urllib.request.Request) -> dict:
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as err:
                if err.code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                    raise SlackError(f"{method}: HTTP {err.code}") from err
                try:
                    retry_after = int(err.headers.get("Retry-After", ""))
                except ValueError:
                    retry_after = 2**attempt
                last_err = err
            except (OSError, http.client.HTTPException, ValueError) as err:
                if attempt == MAX_RETRIES:
                    raise SlackError(f"{method}: {err}") from err
                retry_after = 2**attempt
                last_err = err
            time.sleep(retry_after)
        raise SlackError(f"{method}: retries exhausted") from last_err


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
    sleep(RACE_SETTLE_SECONDS)
    try:
        earliest = min([mine, *find_daily_parents(client, channel, now)], key=ts_key)
    except SlackError as err:
        print(f"::warning::Race-dedup re-list failed, using own parent: {err}", file=sys.stderr)
        return mine
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
    thread_err = ""
    if (env.get("DAILY_THREAD") or "true").lower() != "false":
        try:
            thread_ts = ensure_daily_parent(client, channel, now, sleep)
        except SlackError as err:
            thread_err = str(err)
            print(f"::warning::Could not use the daily thread, posting top level instead: {err}", file=sys.stderr)

    if thread_ts:
        payload["thread_ts"] = thread_ts
    if thread_err:
        blocks = payload.get("blocks", [])
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f":warning: Thread lookup failed: {thread_err}"},
        ]})
        payload["blocks"] = blocks
    reply = client.call("chat.postMessage", **payload)
    return {"ts": reply["ts"], "thread-ts": thread_ts, "channel-id": reply.get("channel", channel)}


def main() -> None:
    env = dict(os.environ)
    try:
        outputs = notify(SlackClient(env["SLACK_BOT_TOKEN"]), env, datetime.now(timezone.utc))
    except SlackError as err:
        print(f"::error::Failed to post notification: {err}", file=sys.stderr)
        sys.exit(1)
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
