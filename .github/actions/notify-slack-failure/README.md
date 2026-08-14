# notify-slack-failure

Composite action that posts a Block Kit failure notification to Slack. Used by
callers of this repo's reusable workflows (e.g. `playwright-cloud.yml`) to
notify on failure, since the reusable workflows themselves stay focused on
running the job and don't own notification policy.

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
        uses: grafana/data-sources-ci-workflows/.github/actions/notify-slack-failure@main
        with:
          title: Cloud E2E tests failed
          repo: ${{ github.repository }}
          stage: nightly
          ref-name: ${{ github.ref_name }}
          actor: ${{ github.actor }}
          sha: ${{ github.sha }}
          run-url: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

## Inputs

| Name                   | Required | Description                                                            |
| ---------------------- | -------- | ------------------------------------------------------------------------ |
| `title`                | Yes      | Short failure title, e.g. "Cloud E2E tests failed".                    |
| `repo`                 | Yes      | Repository the failure originated from (e.g. `github.repository`).     |
| `run-url`              | Yes      | URL of the failed workflow run.                                        |
| `slack-channel-id`     | No       | Slack channel ID to post to. Defaults to `#grafana-ds-plugins-dev`.     |
| `stage`                | No       | Pipeline stage or context the failure happened in.                     |
| `ref-name`             | No       | Git ref name the run was triggered on (e.g. `github.ref_name`).        |
| `actor`                | No       | User that triggered the run (e.g. `github.actor`).                     |
| `sha`                  | No       | Commit SHA associated with the run (e.g. `github.sha`).                |
| `extra-field-1-label`  | No       | Optional label for an extra Block Kit field (e.g. "Grafana Cloud URL"). |
| `extra-field-1-value`  | No       | Optional value for the first extra Block Kit field.                    |
| `extra-field-2-label`  | No       | Optional label for a second extra Block Kit field (e.g. "Version").    |
| `extra-field-2-value`  | No       | Optional value for the second extra Block Kit field.                   |
