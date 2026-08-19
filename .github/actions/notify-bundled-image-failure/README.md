# notify-bundled-image-failure

Composite action that posts a bundled-image build failure notification to Slack.

Defaults to posting to `#ds-release` (`C0BQS6PFW14`).

`cd-bundled.yml` already calls this on failure, so a repo that adopts bundled
images gets the alert without wiring anything up. Set `notify-on-failure: false`
on that workflow to turn it off, or `slack-channel-id` to send it elsewhere.

## Why this is not notify-cloud-e2e-failure

That action reports a *test* failure, and its fields are test-shaped: the Grafana
Cloud URL, the datasource version under test, the run stage. A build failure
needs the plugin, the image reference and the stage that broke. Reusing it would
label an image build as a test run and leave the useful fields empty.

## Why the shared workflow calls this, rather than each caller

`notify-cloud-e2e-failure` is caller-owned, which is how the Elasticsearch
nightly failed ten times in a row without alerting anyone: its `cron.yml` never
added a notify job, and nothing made that visible. Defaulting the notification
on inside `cd-bundled.yml` means a repo has to opt out on purpose instead of
forgetting to opt in.

## Reporting the failed stage

Pass `job-results: ${{ toJSON(needs) }}`. The action reads each job's `result`
and names the ones that did not succeed, so the message says which stage broke
without any per-job list to keep up to date. A skipped job is not treated as a
failure, because the rollout job is skipped on every run that does not ask for
it.

Both the flat `{"job": "result"}` shape and GitHub's nested `needs` shape are
accepted.

## Degrading rather than raising

Every value this action reports comes from an earlier job, and an earlier job can
fail before it produces one. Unparseable or missing input drops the field, or the
stage list, rather than raising. A notification that crashes tells nobody that
the build broke, which is the failure mode this action exists to prevent.

## Example: call it directly

Only needed if you are not using `cd-bundled.yml`. The calling job must grant
`id-token: write` and `contents: read`, because the Slack step authenticates via
OIDC.

```yaml
  notify-failure:
    needs: [build, bundle]
    if: failure()
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: grafana/data-sources-ci-workflows/.github/actions/notify-bundled-image-failure@main
        with:
          repo: ${{ github.repository }}
          plugin-json: ${{ needs.build.outputs.plugin }}
          job-results: ${{ toJSON(needs) }}
          image: ${{ needs.bundle.outputs.image }}
          ref-name: ${{ github.ref_name }}
          actor: ${{ github.actor }}
          sha: ${{ github.sha }}
          run-url: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

Reference the action by its full path, never by a local `./.github/actions/...`
path: a local path resolves against the calling repository, not this one.

## Tests

```bash
cd .github/actions/notify-bundled-image-failure
python3 -m unittest test_build_payload
```
