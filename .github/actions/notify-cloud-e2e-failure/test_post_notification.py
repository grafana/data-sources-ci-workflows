#!/usr/bin/env python3
"""Unit tests for post_notification.

Run with: python3 -m unittest test_post_notification
"""

import unittest
from datetime import datetime, timezone

from post_notification import EVENT_TYPE, SlackError, notify, ts_key

NOW = datetime(2026, 9, 22, 9, 30, tzinfo=timezone.utc)
MIDNIGHT = int(datetime(2026, 9, 22, tzinfo=timezone.utc).timestamp())

ENV = {
    "SLACK_CHANNEL_ID": "C0BQS6PFW14",
    "REPO": "grafana/clickhouse-datasource",
    "RUN_STAGE": "nightly",
    "RUN_URL": "https://github.com/grafana/clickhouse-datasource/actions/runs/1",
}


def parent(ts: str, date: str = "2026-09-22") -> dict:
    return {"ts": ts, "text": "parent", "metadata": {"event_type": EVENT_TYPE, "event_payload": {"date": date}}}


class FakeSlack:
    """Records calls and serves conversations.history from an in-memory channel."""

    def __init__(self, messages=None, fail=()):
        self.messages = list(messages or [])
        self.fail = set(fail)
        self.calls = []
        self.next_ts = MIDNIGHT + 34000
        # Messages another run posts while this one waits for the race to settle.
        self.posted_during_sleep = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method in self.fail:
            raise SlackError(f"{method}: missing_scope")
        if method == "conversations.history":
            oldest = ts_key(params["oldest"])
            return {"ok": True, "messages": [m for m in self.messages if ts_key(m["ts"]) >= oldest]}
        if method == "chat.postMessage":
            self.next_ts += 1
            ts = f"{self.next_ts}.000100"
            message = {"ts": ts, **params}
            if "metadata" in params:
                message["metadata"] = params["metadata"]
            self.messages.append(message)
            return {"ok": True, "ts": ts, "channel": params["channel"]}
        if method == "chat.delete":
            self.messages = [m for m in self.messages if m["ts"] != params["ts"]]
            return {"ok": True}
        raise AssertionError(f"unexpected method {method}")

    def sleep(self, _seconds):
        self.messages.extend(self.posted_during_sleep)

    def posts(self):
        return [p for m, p in self.calls if m == "chat.postMessage"]

    def methods(self):
        return [m for m, _ in self.calls]


class NotifyTest(unittest.TestCase):
    def run_notify(self, slack, env=ENV):
        return notify(slack, env, NOW, sleep=slack.sleep)

    def test_creates_parent_when_none_exists(self):
        slack = FakeSlack()
        outputs = self.run_notify(slack)
        parent_post, reply = slack.posts()
        self.assertEqual(parent_post["metadata"], {"event_type": EVENT_TYPE, "event_payload": {"date": "2026-09-22"}})
        self.assertEqual(reply["thread_ts"], outputs["thread-ts"])
        self.assertEqual(reply["channel"], "C0BQS6PFW14")
        self.assertNotEqual(outputs["ts"], outputs["thread-ts"])
        self.assertEqual(outputs["channel-id"], "C0BQS6PFW14")

    def test_reuses_existing_parent(self):
        existing = f"{MIDNIGHT + 100}.000200"
        slack = FakeSlack(messages=[parent(existing), {"ts": f"{MIDNIGHT + 200}.000000", "text": "chatter"}])
        outputs = self.run_notify(slack)
        (reply,) = slack.posts()
        self.assertEqual(reply["thread_ts"], existing)
        self.assertEqual(outputs["thread-ts"], existing)

    def test_ignores_parent_from_another_day(self):
        slack = FakeSlack(messages=[parent(f"{MIDNIGHT + 100}.000000", date="2026-09-21")])
        self.run_notify(slack)
        self.assertEqual(len(slack.posts()), 2)

    def test_ignores_parents_before_utc_midnight(self):
        slack = FakeSlack(messages=[parent(f"{MIDNIGHT - 60}.000000")])
        self.run_notify(slack)
        self.assertEqual(len(slack.posts()), 2)

    def test_lost_race_deletes_own_parent_and_uses_earlier(self):
        slack = FakeSlack()
        # A concurrent run's parent with an earlier ts shows up after our lookup.
        earlier = f"{MIDNIGHT + 10}.000000"
        slack.posted_during_sleep = [parent(earlier)]
        outputs = self.run_notify(slack)
        mine = slack.posts()[0]
        self.assertIn("chat.delete", slack.methods())
        deleted = next(p for m, p in slack.calls if m == "chat.delete")
        self.assertNotEqual(deleted["ts"], earlier)
        self.assertEqual(outputs["thread-ts"], earlier)
        self.assertEqual(slack.posts()[-1]["thread_ts"], earlier)
        self.assertNotIn("thread_ts", mine)

    def test_won_race_keeps_own_parent(self):
        slack = FakeSlack()
        slack.posted_during_sleep = [parent(f"{MIDNIGHT + 99999}.000000")]
        outputs = self.run_notify(slack)
        self.assertNotIn("chat.delete", slack.methods())
        self.assertEqual(outputs["thread-ts"], slack.posts()[-1]["thread_ts"])
        self.assertLess(ts_key(outputs["thread-ts"]), ts_key(f"{MIDNIGHT + 99999}.000000"))

    def test_history_error_falls_back_to_top_level(self):
        slack = FakeSlack(fail={"conversations.history"})
        outputs = self.run_notify(slack)
        (reply,) = slack.posts()
        self.assertNotIn("thread_ts", reply)
        self.assertEqual(outputs["thread-ts"], "")

    def test_daily_thread_disabled_posts_top_level(self):
        slack = FakeSlack(messages=[parent(f"{MIDNIGHT + 100}.000000")])
        outputs = self.run_notify(slack, {**ENV, "DAILY_THREAD": "false"})
        self.assertEqual(slack.methods(), ["chat.postMessage"])
        self.assertNotIn("thread_ts", slack.posts()[0])
        self.assertEqual(outputs["thread-ts"], "")

    def test_reply_failure_raises(self):
        slack = FakeSlack(fail={"chat.postMessage"})
        with self.assertRaises(SlackError):
            self.run_notify(slack)

    def test_ts_key_orders_exactly(self):
        self.assertLess(ts_key("1700000000.000009"), ts_key("1700000000.000010"))


if __name__ == "__main__":
    unittest.main()
