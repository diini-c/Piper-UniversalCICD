# Piper

Auto-detects a project's stack, installs its dependencies, runs its tests, and
generates a CI pipeline that matches what it found.

## Requirements

- Python 3.10 or newer

## Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Commands

### `piper scan`

Detect the stack and write a JSON report.

```bash
piper scan --root . --report .pipeline/detection.json
piper scan --root . --depth 0      # skip the search for nested projects
```

Reports the detected languages, framework, deploy target, package manager and
test runners, plus any nested projects found in a monorepo.

### `piper install`

Install dependencies for everything that was detected.

```bash
piper install
piper install --dry-run            # print the commands without running them
```

Prefers reproducible installs: `npm ci`, `pnpm install --frozen-lockfile` and
`yarn install --frozen-lockfile` are used whenever a lockfile is present.

### `piper test`

Run the test suites that were detected.

```bash
piper test
piper test --dry-run
```

### `piper generate`

Generate a CI pipeline for the detected stack.

```bash
piper generate                     # writes .github/workflows/ci.yml
piper generate --stdout            # print instead of writing
piper generate --name Build --branch develop
piper generate --check             # fail if the file on disk is out of date
piper generate --depth 0           # root only, ignore nested projects
```

`--check` is intended for CI: it exits non-zero when the committed pipeline no
longer matches the project, which catches a stack change that nobody regenerated.

#### Monorepos

Every nested project found by `scan` gets its own job, running in its own
directory and gated on whether that directory actually changed:

```yaml
  services_api_python:
    name: services/api · Python
    needs: changes
    if: needs.changes.outputs.services_api == 'true'
    defaults:
      run:
        working-directory: services/api
```

The gate is a `changes` job using `dorny/paths-filter`. GitHub only supports
path filters at workflow level, never per job, so the filtering has to run as a
job the others depend on. A job skipped this way still reports a conclusion,
which keeps it usable as a required status check — a workflow-level `paths:`
filter would report nothing at all and leave branch protection waiting forever.

## What gets detected

| Category | Recognised |
| --- | --- |
| Languages | Node, Python, Rust, Go |
| Node frameworks | Next, Nuxt, Remix, Angular, Svelte, Vue, React, Express, Fastify |
| Python frameworks | Django, FastAPI, Flask, Starlette, Pyramid |
| Package managers | npm, pnpm, yarn |
| Test runners | pytest, vitest, jest, mocha, Playwright, Cypress, `cargo test`, `go test` |
| Deploy targets | Vercel, Netlify, Fly, Heroku, Railway, Docker |

Frameworks are identified from the dependencies declared in `package.json` or
`pyproject.toml`/`requirements.txt`, not from config filenames — a
`vite.config.ts` tells you the build tool, not whether the app is Vue or React.

## Development

```bash
python -m pytest                   # tests
mypy                               # type check (strict)
ruff check .                       # lint
black .                            # format
pre-commit run --all-files         # everything the hooks enforce
```
