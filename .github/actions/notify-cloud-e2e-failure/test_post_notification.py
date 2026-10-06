#!/usr/bin/env python3
"""Unit tests for post_notification.

Run with: python3 -m unittest test_post_notification
"""

import http.client
import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import call, patch
from urllib.error import HTTPError

from post_notification import EVENT_TYPE, MAX_RETRIES, SlackClient, SlackError, notify, ts_key

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
    """Records calls and mirrors Slack conversations.history behavior.

    Newest-first ordering, metadata stripped without include_all_metadata,
    and cursor-based pagination at ``page_size`` messages per page.
    """

    def __init__(self, messages=None, fail=(), page_size=200):
        self.messages = list(messages or [])
        self.fail = set(fail)
        self.calls = []
        self.post_results = []
        self.next_ts = MIDNIGHT + 34000
        self.posted_during_sleep = []
        self.page_size = page_size

    def call(self, method, **params):
        self.calls.append((method, params))
        if method in self.fail:
            raise SlackError(f"{method}: missing_scope")
        if method == "conversations.history":
            return self._conversations_history(params)
        if method == "chat.postMessage":
            self.next_ts += 1
            ts = f"{self.next_ts}.000100"
            message = {"ts": ts, **params}
            if "metadata" in params:
                message["metadata"] = params["metadata"]
            self.messages.append(message)
            result = {"ok": True, "ts": ts, "channel": params["channel"]}
            self.post_results.append(result)
            return result
        if method == "chat.delete":
            self.messages = [m for m in self.messages if m["ts"] != params["ts"]]
            return {"ok": True}
        raise AssertionError(f"unexpected method {method}")

    def _conversations_history(self, params):
        oldest = ts_key(params.get("oldest", "0"))
        matching = sorted(
            [m for m in self.messages if ts_key(m["ts"]) >= oldest],
            key=lambda m: ts_key(m["ts"]),
            reverse=True,
        )
        include_metadata = params.get("include_all_metadata") == "true"
        if not include_metadata:
            matching = [{k: v for k, v in m.items() if k != "metadata"} for m in matching]
        cursor = int(params["cursor"]) if params.get("cursor") else 0
        page = matching[cursor : cursor + self.page_size]
        resp = {"ok": True, "messages": page}
        if cursor + self.page_size < len(matching):
            resp["response_metadata"] = {"next_cursor": str(cursor + self.page_size)}
        return resp

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
        earlier = f"{MIDNIGHT + 10}.000000"
        slack.posted_during_sleep = [parent(earlier)]
        outputs = self.run_notify(slack)
        our_parent_ts = slack.post_results[0]["ts"]
        self.assertIn("chat.delete", slack.methods())
        deleted = next(p for m, p in slack.calls if m == "chat.delete")
        self.assertEqual(deleted["ts"], our_parent_ts)
        self.assertEqual(outputs["thread-ts"], earlier)
        self.assertEqual(slack.posts()[-1]["thread_ts"], earlier)
        self.assertNotIn("thread_ts", slack.posts()[0])

    def test_lost_race_delete_fails_still_uses_earlier(self):
        slack = FakeSlack(fail={"chat.delete"})
        earlier = f"{MIDNIGHT + 10}.000000"
        slack.posted_during_sleep = [parent(earlier)]
        outputs = self.run_notify(slack)
        self.assertEqual(outputs["thread-ts"], earlier)

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
        self.assertIn("missing_scope", reply["blocks"][-1]["elements"][0]["text"])

    def test_parent_post_failure_falls_back_to_top_level(self):
        slack = FakeSlack()
        real_call = slack.call

        def fail_parent(method, **params):
            if method == "chat.postMessage" and "metadata" in params:
                raise SlackError("chat.postMessage: parent failed")
            return real_call(method, **params)

        with patch.object(slack, "call", side_effect=fail_parent):
            outputs = self.run_notify(slack)
        (reply,) = slack.posts()
        self.assertNotIn("thread_ts", reply)
        self.assertEqual(outputs["thread-ts"], "")
        self.assertEqual(outputs["ts"], slack.post_results[0]["ts"])
        self.assertIn("parent failed", reply["blocks"][-1]["elements"][0]["text"])

    def test_race_relist_failure_replies_to_own_parent(self):
        slack = FakeSlack()

        def fail_relist(_seconds):
            slack.fail.add("conversations.history")

        outputs = notify(slack, ENV, NOW, sleep=fail_relist)
        parent_post, reply = slack.posts()
        self.assertIn("metadata", parent_post)
        self.assertEqual(reply["thread_ts"], slack.post_results[0]["ts"])
        self.assertEqual(outputs["thread-ts"], slack.post_results[0]["ts"])

    def test_daily_thread_disabled_posts_top_level(self):
        slack = FakeSlack(messages=[parent(f"{MIDNIGHT + 100}.000000")])
        outputs = self.run_notify(slack, {**ENV, "DAILY_THREAD": "false"})
        self.assertEqual(slack.methods(), ["chat.postMessage"])
        self.assertNotIn("thread_ts", slack.posts()[0])
        self.assertEqual(outputs["thread-ts"], "")

    def test_empty_daily_thread_defaults_to_threaded(self):
        existing = f"{MIDNIGHT + 100}.000000"
        slack = FakeSlack(messages=[parent(existing)])
        outputs = self.run_notify(slack, {**ENV, "DAILY_THREAD": ""})
        self.assertEqual(outputs["thread-ts"], existing)

    def test_reply_failure_raises(self):
        slack = FakeSlack(messages=[parent(f"{MIDNIGHT + 100}.000000")], fail={"chat.postMessage"})
        with self.assertRaises(SlackError):
            self.run_notify(slack)

    def test_finds_parent_on_second_page(self):
        chatter = [{"ts": f"{MIDNIGHT + i}.000000", "text": "chatter"} for i in range(2, 5)]
        p = parent(f"{MIDNIGHT + 1}.000000")
        slack = FakeSlack(messages=chatter + [p], page_size=2)
        outputs = self.run_notify(slack)
        (reply,) = slack.posts()
        self.assertEqual(reply["thread_ts"], p["ts"])
        self.assertEqual(outputs["thread-ts"], p["ts"])
        history_calls = [params for method, params in slack.calls if method == "conversations.history"]
        self.assertEqual([params["cursor"] for params in history_calls], [None, "2"])

    def test_reuses_earliest_of_multiple_existing_parents(self):
        earlier = f"{MIDNIGHT + 10}.000000"
        later = f"{MIDNIGHT + 20}.000000"
        slack = FakeSlack(messages=[parent(earlier), parent(later)])
        outputs = self.run_notify(slack)
        (reply,) = slack.posts()
        self.assertEqual(reply["thread_ts"], earlier)
        self.assertEqual(outputs["thread-ts"], earlier)

    def test_ts_key_orders_exactly(self):
        self.assertLess(ts_key("1700000000.000009"), ts_key("1700000000.000010"))


class SlackClientTest(unittest.TestCase):
    def test_rate_limit_honors_retry_after_over_thirty_seconds(self):
        error = HTTPError("https://slack.com/api/chat.postMessage", 429, "limited", {"Retry-After": "60"}, None)
        self.addCleanup(error.close)
        with patch("post_notification.urllib.request.urlopen", side_effect=[error, io.BytesIO(b'{"ok":true}')]) as request:
            with patch("post_notification.time.sleep") as sleep:
                self.assertEqual(SlackClient("dummy").call("chat.postMessage", channel="test", text="test"), {"ok": True})
        sleep.assert_called_once_with(60)
        self.assertEqual(request.call_count, 2)

    def test_transient_http_errors_stop_after_retry_limit(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                error = HTTPError("https://slack.com/api/chat.postMessage", status, "retry", {}, None)
                self.addCleanup(error.close)
                with patch("post_notification.urllib.request.urlopen", side_effect=error) as request:
                    with patch("post_notification.time.sleep") as sleep:
                        with self.assertRaisesRegex(SlackError, f"HTTP {status}"):
                            SlackClient("dummy").call("chat.postMessage")
                self.assertEqual(request.call_count, MAX_RETRIES + 1)
                self.assertEqual(sleep.call_args_list, [call(2**attempt) for attempt in range(MAX_RETRIES)])

    def test_non_retryable_http_error_fails_immediately(self):
        error = HTTPError("https://slack.com/api/chat.postMessage", 403, "forbidden", {}, None)
        self.addCleanup(error.close)
        with patch("post_notification.urllib.request.urlopen", side_effect=error) as request:
            with patch("post_notification.time.sleep") as sleep:
                with self.assertRaisesRegex(SlackError, "HTTP 403"):
                    SlackClient("dummy").call("chat.postMessage")
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_decode_and_incomplete_read_errors_become_slack_errors(self):
        for error in (json.JSONDecodeError("invalid", "", 0), http.client.IncompleteRead(b"partial")):
            with self.subTest(error=type(error).__name__):
                with patch("post_notification.urllib.request.urlopen", side_effect=error) as request:
                    with patch("post_notification.time.sleep"):
                        with self.assertRaises(SlackError):
                            SlackClient("dummy").call("conversations.history")
                self.assertEqual(request.call_count, MAX_RETRIES + 1)


if __name__ == "__main__":
    unittest.main()
