# notify-cloud-e2e-failure

Composite action that posts a Cloud E2E test failure notification to Slack.
Used by callers of `playwright-cloud.yml` (e.g. a plugin's `cron.yml`) to
notify on failure, since the reusable workflow itself stays focused on
running the tests and doesn't own notification policy.

Defaults to posting to `#grafana-ds-plugins-dev` (`C0APH909GFK`).

## Example: notify on a nightly Cloud E2E failure

```yaml
name: Scheduled Cloud E2E tests

on:
  schedule:
    - cron: '0 9 * * *'

permissions:
  contents: read
  id-token: write

jobs:
  playwright-cloud:
    uses: grafana/data-sources-ci-workflows/.github/workflows/playwright-cloud.yml@main
    secrets: inherit
    permissions:
      contents: read
      id-token: write
    with:
      run-stage: nightly
      pdc-network-name: datasources-pdc-network-aws-datasourcese2e
      repo-secrets: |
        DS_INSTANCE_HOST=ds-instance:host
        DS_INSTANCE_PASSWORD=ds-instance:password
        DS_INSTANCE_PORT=ds-instance:port
        DS_INSTANCE_USERNAME=ds-instance:username

  notify-slack:
    name: Notify Slack on failure
    needs: playwright-cloud
    if: failure()
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      id-token: write
    steps:
      - name: Notify Slack on failure
        uses: grafana/data-sources-ci-workflows/.github/actions/notify-cloud-e2e-failure@main
        with:
          repo: ${{ github.repository }}
          run-stage: nightly
          ref-name: ${{ github.ref_name }}
          actor: ${{ github.actor }}
          sha: ${{ github.sha }}
          run-url: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

## Inputs

| Name                 | Required | Description                                                                                             |
| -------------------- | -------- | --------------------------------------------------------------------------------------------------------- |
| `repo`               | Yes      | Repository the failure originated from (e.g. `github.repository`).                                      |
| `run-url`            | Yes      | URL of the failed workflow run.                                                                          |
| `slack-channel-id`   | No       | Slack channel ID to post to. Defaults to `#grafana-ds-plugins-dev`.                                      |
| `run-stage`          | No       | Rollout stage the run represents (`pr`, `main`, `nightly`, `dev0`, `ops`, `prod0`-`prod4`, `catalog`).   |
| `grafana-url`        | No       | Grafana Cloud instance URL the tests ran against.                                                        |
| `datasource-version` | No       | Datasource plugin version under test.                                                                   |
| `ref-name`           | No       | Git ref name the run was triggered on (e.g. `github.ref_name`).                                         |
| `actor`              | No       | User that triggered the run (e.g. `github.actor`).                                                       |
| `sha`                | No       | Commit SHA associated with the run (e.g. `github.sha`).                                                 |

## Payload and tests

The Slack Block Kit payload is built by `build_payload.py`, which reads the inputs from
the environment and prints the `chat.postMessage` payload as JSON for the Slack step to
send. Keeping it in a standalone module rather than inline in `action.yml` means the field
selection (including the optional `grafana-url` / `datasource-version` rows) and the mrkdwn
escaping are lintable and unit-tested.

Run the tests from the action directory:

```sh
python3 -m unittest test_build_payload
```
