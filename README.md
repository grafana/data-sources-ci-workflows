# data-sources-ci-workflows

Reusable GitHub Actions workflows for testing and deploying **Grafana Labs-owned** data source plugins in CI. Maintained by [@grafana/data-sources-plugins](https://github.com/orgs/grafana/teams/data-sources-plugins).

## Releases

[release-please](https://github.com/googleapis/release-please) cuts releases from the Conventional Commits PR titles merged to `main`. After every push to `main`, it opens or updates a release PR titled `chore(main): release X.Y.Z` with the next `CHANGELOG.md` section. Merging that PR creates the `vX.Y.Z` tag and the GitHub release.

Callers pin a workflow to a release tag, or to `main` for the latest merged commit:

```yaml
uses: grafana/data-sources-ci-workflows/.github/workflows/cd-dev.yml@v1.0.0
```

`cd-bundled.yml` runs its composite actions from `main` whatever ref the caller pins, so a tag does not fix it.

release-please owns `CHANGELOG.md` and `.release-please-manifest.json`. To change the wording of an entry, edit `CHANGELOG.md` on the release PR branch before you merge it. Do this last, because release-please rewrites the branch on the next push to `main`.

### Pull request titles

The repository squash-merges every PR with the PR title as the commit subject, so the title is the conventional commit. The `PR Conventional Commit Validation` workflow fails a PR whose title does not match this format:

```text
type(scope): subject
```

The scope is optional. The subject must start with a lowercase letter. The type decides the changelog section and the version bump of the next release:

| Type       | Changelog section           | Version effect |
| ---------- | --------------------------- | -------------- |
| `feat`     | 🎉 Features                 | minor          |
| `fix`      | 🐛 Bug Fixes                | patch          |
| `perf`     | ⚡ Performance Improvements | patch          |
| `refactor` | ♻️ Code Refactoring         | patch          |
| `docs`     | 📝 Documentation            | patch          |
| `test`     | ✅ Tests                    | patch          |
| `build`    | 🏗️ Builds                   | patch          |
| `ci`       | 🤖 Continuous Integration   | patch          |
| `revert`   | ⏪ Reverts                  | patch          |
| `chore`    | hidden                      | none           |

A `!` before the colon marks a breaking change and bumps the major version, for example `feat(cd-dev)!: remove the deploy input`. Routine dependency updates land as `chore(deps)` and do not produce a release on their own. Security updates from vulnerability alerts land as `fix(deps)` and produce a patch release. To cut a release with no releasable commits, add a `Release-As: x.y.z` footer to the squash commit message when you merge a PR.
