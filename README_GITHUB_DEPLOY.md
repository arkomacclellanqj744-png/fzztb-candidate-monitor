# GitHub Actions Deployment

This repository checks the Fuzhou tender portal every 5 minutes for the target project's candidate notice.

## Required secret

Create a repository secret named:

```text
PUSHPLUS_TOKEN
```

Its value should be your PushPlus token.

## Run

After pushing this repository to GitHub:

1. Open the GitHub repository.
2. Go to `Settings` -> `Secrets and variables` -> `Actions`.
3. Add `PUSHPLUS_TOKEN`.
4. Go to `Actions`.
5. Enable workflows if GitHub asks.
6. Open `Monitor tender candidate notice`.
7. Click `Run workflow` once to test.

The schedule is defined in `.github/workflows/monitor.yml` as `*/5 * * * *`.
