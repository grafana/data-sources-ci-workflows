#!/usr/bin/env python3
"""Unit tests for build_payload.

Run with: python3 -m unittest test_build_payload
"""

import unittest

from build_payload import build_payload, slack_escape

BASE_ENV = {
    "SLACK_CHANNEL_ID": "C0APH909GFK",
    "REPO": "grafana/clickhouse-datasource",
    "RUN_STAGE": "nightly",
    "REF_NAME": "main",
    "ACTOR": "octocat",
    "SHA": "0123456789abcdef",
    "RUN_URL": "https://github.com/grafana/clickhouse-datasource/actions/runs/1",
}


def field_texts(payload: dict) -> list[str]:
    (fields_block,) = (b for b in payload["blocks"] if "fields" in b)
    return [f["text"] for f in fields_block["fields"]]


class BuildPayloadTest(unittest.TestCase):
    def test_channel_and_fallback(self):
        payload = build_payload(BASE_ENV)
        self.assertEqual(payload["channel"], "C0APH909GFK")
        self.assertEqual(payload["text"], "Cloud E2E tests failed for grafana/clickhouse-datasource (nightly)")

    def test_base_fields_present_without_optionals(self):
        texts = field_texts(build_payload(BASE_ENV))
        self.assertEqual(
            texts,
            [
                "*Repository:*\ngrafana/clickhouse-datasource",
                "*Run stage:*\nnightly",
                "*Branch:*\nmain",
                "*Triggered by:*\noctocat",
                "*Commit:*\n`01234567`",
            ],
        )

    def test_optional_fields_appear_when_set(self):
        env = {**BASE_ENV, "GRAFANA_URL": "https://example.grafana.net", "DATASOURCE_VERSION": "4.21.0"}
        texts = field_texts(build_payload(env))
        self.assertIn("*Grafana Cloud URL:*\nhttps://example.grafana.net", texts)
        self.assertIn("*Datasource version:*\n4.21.0", texts)

    def test_optional_fields_omitted_when_blank(self):
        env = {**BASE_ENV, "GRAFANA_URL": "", "DATASOURCE_VERSION": ""}
        texts = field_texts(build_payload(env))
        self.assertFalse(any("Grafana Cloud URL" in t or "Datasource version" in t for t in texts))

    def test_sha_truncated_to_eight(self):
        texts = field_texts(build_payload(BASE_ENV))
        self.assertIn("*Commit:*\n`01234567`", texts)

    def test_run_url_block(self):
        payload = build_payload(BASE_ENV)
        link_blocks = [b for b in payload["blocks"] if b.get("text", {}).get("text", "").startswith("<http")]
        self.assertEqual(len(link_blocks), 1)
        self.assertIn("|View the failed run>", link_blocks[0]["text"]["text"])

    def test_slack_escape(self):
        self.assertEqual(slack_escape("a & b < c > d"), "a &amp; b &lt; c &gt; d")

    def test_control_characters_escaped_in_fields(self):
        env = {**BASE_ENV, "ACTOR": "a<b>&c"}
        texts = field_texts(build_payload(env))
        self.assertIn("*Triggered by:*\na&lt;b&gt;&amp;c", texts)


if __name__ == "__main__":
    unittest.main()
