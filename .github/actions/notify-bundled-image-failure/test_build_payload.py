#!/usr/bin/env python3
"""Unit tests for build_payload.

Run with: python3 -m unittest test_build_payload
"""

import json
import unittest

from build_payload import DEFAULT_CHANNEL, build_payload, failed_stages, plugin_identity, slack_escape

BASE_ENV = {
    "SLACK_CHANNEL_ID": "C0BQS6PFW14",
    "REPO": "grafana/clickhouse-datasource",
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
        self.assertEqual(payload["channel"], "C0BQS6PFW14")
        self.assertEqual(payload["text"], "Bundled image build failed for grafana/clickhouse-datasource at unknown")

    def test_base_fields_present_without_optionals(self):
        texts = field_texts(build_payload(BASE_ENV))
        self.assertEqual(
            texts,
            [
                "*Repository:*\ngrafana/clickhouse-datasource",
                "*Failed at:*\nunknown",
                "*Branch:*\nmain",
                "*Triggered by:*\noctocat",
                "*Commit:*\n`01234567`",
            ],
        )

    def test_optional_fields_appear_when_set(self):
        env = {
            **BASE_ENV,
            "PLUGIN_JSON": json.dumps({"id": "grafana-clickhouse-datasource", "version": "4.21.0"}),
            "IMAGE": "us-docker.pkg.dev/x/y:4.21.0-13.3.0-abc1234-amd64",
            "GRAFANA_IMAGE": "grafana-pro:13.3.0",
        }
        texts = field_texts(build_payload(env))
        self.assertIn("*Plugin:*\ngrafana-clickhouse-datasource 4.21.0", texts)
        self.assertIn("*Image:*\n`us-docker.pkg.dev/x/y:4.21.0-13.3.0-abc1234-amd64`", texts)
        self.assertIn("*Grafana base image:*\n`grafana-pro:13.3.0`", texts)

    def test_optional_fields_omitted_when_blank(self):
        env = {**BASE_ENV, "PLUGIN_JSON": "", "IMAGE": "", "GRAFANA_IMAGE": ""}
        texts = field_texts(build_payload(env))
        self.assertFalse(any("Plugin:" in t or "Image:" in t for t in texts))

    def test_plugin_json_unparseable_degrades(self):
        # The build job can fail before it emits its output. A notification that
        # raises here would mean nobody hears about the failure at all.
        for raw in ("", "not json", "[]", "null", '"a string"'):
            with self.subTest(raw=raw):
                self.assertEqual(plugin_identity(raw), ("", ""))
                payload = build_payload({**BASE_ENV, "PLUGIN_JSON": raw})
                self.assertFalse(any("Plugin:" in t for t in field_texts(payload)))

    def test_plugin_version_absent_still_reports_id(self):
        env = {**BASE_ENV, "PLUGIN_JSON": json.dumps({"id": "grafana-clickhouse-datasource"})}
        self.assertIn("*Plugin:*\ngrafana-clickhouse-datasource", field_texts(build_payload(env)))

    def test_failed_stages_names_only_failures(self):
        results = json.dumps({"build": "success", "resolve-grafana-image": "success", "bundle": "failure"})
        self.assertEqual(failed_stages(results), ["bundle"])
        self.assertIn("*Failed at:*\nbundle", field_texts(build_payload({**BASE_ENV, "JOB_RESULTS": results})))

    def test_skipped_is_not_a_failure(self):
        # The rollout job is skipped on every run that does not ask for it.
        # Reporting that as the failing stage would point at the wrong place.
        results = json.dumps({"build": "failure", "trigger-argo": "skipped"})
        self.assertEqual(failed_stages(results), ["build"])

    def test_several_failed_stages_are_joined(self):
        results = json.dumps({"build": "failure", "bundle": "cancelled"})
        self.assertEqual(failed_stages(results), ["build", "bundle"])
        self.assertIn("*Failed at:*\nbuild, bundle", field_texts(build_payload({**BASE_ENV, "JOB_RESULTS": results})))

    def test_accepts_the_github_needs_shape(self):
        # toJSON(needs) nests each result under an object alongside outputs, so
        # the parser has to read .result rather than compare the object itself.
        results = json.dumps(
            {
                "build": {"result": "success", "outputs": {"plugin": "{}"}},
                "resolve-grafana-image": {"result": "success", "outputs": {}},
                "bundle": {"result": "failure", "outputs": {}},
                "trigger-argo": {"result": "skipped", "outputs": {}},
            }
        )
        self.assertEqual(failed_stages(results), ["bundle"])

    def test_job_results_unparseable_degrades_to_unknown(self):
        for raw in ("", "not json", "[]"):
            with self.subTest(raw=raw):
                self.assertEqual(failed_stages(raw), [])
                self.assertIn("*Failed at:*\nunknown", field_texts(build_payload({**BASE_ENV, "JOB_RESULTS": raw})))

    def test_channel_falls_back_when_empty_or_missing(self):
        # A caller that passes the input as an empty string bypasses the composite
        # default, which is how run 32415750224's notification posted to channel "".
        for env in ({**BASE_ENV, "SLACK_CHANNEL_ID": ""}, {k: v for k, v in BASE_ENV.items() if k != "SLACK_CHANNEL_ID"}):
            with self.subTest(has_key="SLACK_CHANNEL_ID" in env):
                self.assertEqual(build_payload(env)["channel"], DEFAULT_CHANNEL)

    def test_sha_truncated_to_eight(self):
        self.assertIn("*Commit:*\n`01234567`", field_texts(build_payload(BASE_ENV)))

    def test_run_url_block(self):
        payload = build_payload(BASE_ENV)
        link_blocks = [b for b in payload["blocks"] if b.get("text", {}).get("text", "").startswith("<http")]
        self.assertEqual(len(link_blocks), 1)
        self.assertIn("|View the failed run>", link_blocks[0]["text"]["text"])

    def test_slack_escape(self):
        self.assertEqual(slack_escape("a & b < c > d"), "a &amp; b &lt; c &gt; d")

    def test_control_characters_escaped_in_fields(self):
        texts = field_texts(build_payload({**BASE_ENV, "ACTOR": "a<b>&c"}))
        self.assertIn("*Triggered by:*\na&lt;b&gt;&amp;c", texts)

    def test_payload_is_json_serialisable(self):
        json.dumps(build_payload(BASE_ENV))


if __name__ == "__main__":
    unittest.main()
