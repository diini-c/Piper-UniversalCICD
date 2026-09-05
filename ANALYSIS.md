# Piper — codebase analysis and change report

Nothing here is committed. Everything sits in the working tree so you can read
`git diff`, keep what you want, and drop the rest.

---

## 1. The headline finding

**`pyproject.toml` did not parse, and that took the entire toolchain down.**

Commit `0a76ef2 "chore: expose piper cli entrypoint"` appended a second
`[project.scripts]` table to the bottom of the file. One was already declared.
TOML forbids declaring the same table twice, so every tool that reads
`pyproject.toml` failed at the parse step:

| Tool | Before | Why |
| --- | --- | --- |
| `pip install .` | **fails** | `TOMLDecodeError: Cannot declare ('project', 'scripts') twice (at line 46)` |
| `ruff check` | **fails** | `TOML parse error at line 46 — duplicate key` |
| `black` | **fails** | `Error reading configuration file` |
| `pytest` | **fails** | `ERROR: pyproject.toml: Cannot declare ... twice` |
| `mypy` | warns, continues | reads its config leniently |

`pre-commit run --all-files` — the command the README tells a new contributor to
run — would have failed on three of its four hooks.

Fixed by removing the duplicate and moving `[project.scripts]` up next to
`[project]`, where its being orphaned at the bottom of the file can't invite a
second copy again.

**Prevention:** `check-toml` is now the first pre-commit hook. It catches this
exact class of error before it can be committed.

---

## 2. Everything else that was wrong

### Correctness

| # | Issue | Evidence |
| --- | --- | --- |
| 1 | `vite.config.ts` was hardcoded to mean `"vue"`. Vite is the default build tool for React, Svelte and Solid too, so any Vite+React project was reported as Vue. | `detect.py:57` (old) |
| 2 | `run()` used `check=True`, so a failed install raised `CalledProcessError` as a raw traceback. A missing `npm`/`cargo`/`go` raised `FileNotFoundError` — no message about what to install. | `deps.py:12` (old) |
| 3 | `pprint(detection)` left in `report.py` printed the raw dict above every table. Debug output shipped as a feature. | `report.py:17` (old) |
| 4 | Detection only ever looked at the top-level directory. Any monorepo reduced to whatever happened to sit at the root. | `detect.py:36-43` (old) |
| 5 | `[tool.mypy]` set both `packages` and `files` (contradictory) and pinned `python_version = "3.11"` while `requires-python` said `>=3.10` — so 3.10 incompatibilities could not be caught. | `pyproject.toml` |
| 6 | `pyproject.toml` declared `pyyaml`, `jinja2`, `pathspec` and `tomli-w` as runtime dependencies. **None of them were imported anywhere.** Four dependencies installed for nothing. | `grep` over `src/` |
| 7 | `editorconfig` was missing its leading dot, so no editor ever read it. Its `indent_size = 2` also contradicted Black's 4-space Python formatting. | `ls -a` |
| 8 | README had an unclosed ```` ``` ```` fence — everything from "Prerequisites" down rendered as one code block. It also claimed "Python 3.8+" against a `>=3.10` floor. | `README.md:12` |

### Missing

| # | Gap |
| --- | --- |
| 9 | **No tests at all.** `pyproject.toml` set `testpaths = ["tests"]` and mypy set `files = ["src", "tests"]` — both pointed at a directory that did not exist. |
| 10 | **No CI.** A CI/CD tool with no `.github/workflows/`. |
| 11 | **No pipeline generation.** The project description promises "generates adaptive CI pipelines" and the README promises "runs tests". Neither command existed — only `scan` and `install`. |
| 12 | `isort` was configured in `pyproject.toml` but was not in `.pre-commit-config.yaml`, so import order was never enforced. Ruff found 3 unsorted import blocks. |

### Repo hygiene

| # | Issue |
| --- | --- |
| 13 | `src/piper.egg-info/` (6 files) is committed despite `*.egg-info/` being in `.gitignore`. Ignore rules do not apply to already-tracked files. It is regenerated on every editable install, so it churns on every diff. |
| 14 | `.pipeline/detection.json` is committed despite `.pipeline/` being in `.gitignore`. Same cause. |
| 15 | `test_report.json` — a stray scan artifact — is committed at the repo root and matched no ignore rule at all. |

Items 13–15 need `git rm --cached`, which touches the git index, so **I did not
do it**. `scripts/untrack-build-artifacts.sh` does it in one step when you're ready.

---

## 3. What changed

### Fixed

- `pyproject.toml` parses. Toolchain restored.
- Framework detection reads **declared dependencies** (`package.json`,
  `pyproject.toml`, `requirements.txt`, Poetry blocks) instead of guessing from
  filenames. Vite is reported as `vite` only when nothing more specific is found.
- `run()` checks `PATH` first and raises `MissingToolError` with an install hint
  (`pnpm` → "run `corepack enable`"), and `CommandFailedError` on a non-zero exit.
  Both are `PiperError`, which the CLI renders as a clean message, not a traceback.
- Removed the stray `pprint`.
- `scan()` finds nested projects, honouring `.gitignore` (via `pathspec`, now
  actually used) and skipping `node_modules`, `venv`, `target` and friends.
- mypy targets 3.10 and passes `--strict` on `src` **and** `tests`.
- `.editorconfig` renamed, with a `[*.py] indent_size = 4` override.
- README rewritten.

### Added

| Thing | Notes |
| --- | --- |
| `piper generate` | Renders a GitHub Actions workflow from the detection. Jinja2 + PyYAML — two of the four dead dependencies now earn their place. |
| `piper test` | Runs the detected suites. |
| `--dry-run` on `install`/`test` | Prints the plan instead of running it. |
| `--check` on `generate` | Exits non-zero when the committed pipeline no longer matches the project. Meant for CI. |
| `tests/` | **102 tests, 94% coverage.** |
| `.github/workflows/ci.yml` | lint · test (3.10–3.13) · dogfood · build. |
| `src/piper/errors.py` | Typed error hierarchy. |
| `scripts/untrack-build-artifacts.sh` | The index cleanup I left for you. |

### Design note: planning is separate from execution

`plan_install()` and `plan_tests()` are pure functions returning
`list[list[str]]`. `install_for()` and `run_tests()` execute them. That split is
what makes `--dry-run` a two-line feature and lets the interesting logic be
tested without spawning a single subprocess.

(These were named `install_plan`/`test_plan`. `test_plan` had to be renamed —
pytest collects *any* module-level name starting with `test_` as a test case, so
importing it into a test file made pytest error with `fixture 'detection' not
found`. Verb-first naming avoids the collision.)

---

## 4. Three bugs the tests caught in my own code

Worth listing, because they are the reason to trust the rest:

1. **`job.matrix.values` returned a method.** Jinja resolves attribute access
   before item lookup, so a dict key named `values` silently returned
   `dict.values` — `TypeError: 'builtin_function_or_method' object is not
   iterable`. Renamed to `options`. The same trap applies to `keys`, `items`
   and `with`; `step.with` was renamed to `step.params` for the same reason.

2. **Unquoted `3.10` in a YAML matrix parses as the float `3.1`.** The job would
   have silently run on the wrong interpreter. Every matrix value is now quoted,
   with `test_python_versions_stay_strings` asserting it.

3. **`StrictUndefined` rejected run-only steps** that had no `uses` key. Fixed by
   normalising every step to the full key set in Python, so the strict template
   check stays on rather than being loosened.

---

## 5. Verification

Run from the repo root:

```
pyproject parses          OK
ruff check .              All checks passed
black --check .           14 files unchanged
mypy (strict, src+tests)  Success: no issues found in 14 source files
pytest                    102 passed
coverage                  94%
python -m build           sdist + wheel built
twine check dist/*        PASSED
```

CLI, end to end on this repo and on a synthetic pnpm/Next/Rust/Docker monorepo
fixture: `scan`, `install --dry-run`, `test --dry-run`, `generate --stdout` all OK.

Coverage by module:

```
src/piper/__init__.py     100%
src/piper/errors.py       100%
src/piper/report.py       100%
src/piper/detect.py        95%
src/piper/generate.py      94%
src/piper/cli.py           93%
src/piper/deps.py          91%
                    TOTAL  94%
```

### One expected non-green result

`piper generate --check` reports `.github/workflows/ci.yml` is out of date. That
is **correct and intentional**: the committed workflow is hand-written and richer
than generated output (it adds lint, dogfood and build jobs). It demonstrates
that drift detection works. Don't wire `--check` into this repo's own CI unless
you decide the generated pipeline should be the canonical one.

---

## 6. Open questions for you

1. **Does the generated workflow become canonical for this repo?** If yes, the
   hand-written extras need to move into the generator and `--check` goes into CI.
   If no, they stay separate and `--check` is a feature for Piper's *users* only.
2. **`tomli-w` is still an unused dependency.** It writes TOML. Nothing writes
   TOML yet. Drop it, or is there a planned `piper init` that edits `pyproject.toml`?
3. **Node version matrix is hardcoded to 20/22.** Reading `engines.node` from
   `package.json` would mirror what the Python side does with `requires-python`.
4. **Workspaces are detected but not used.** `scan` reports them; `generate`
   still only builds jobs for the root. Per-workspace jobs are the obvious next step.

---

## 7. Suggested phasing for what's left

**Phase 1 — merge what's here.** Review the diff, run
`scripts/untrack-build-artifacts.sh`, commit. Depends on nothing. Small.

**Phase 2 — close the loop on workspaces.** Generate one job per detected
workspace with `defaults.run.working-directory` set, plus path filters so a
change under `apps/web` doesn't rebuild `services/api`. Depends on Phase 1.
Moderate — the detection already produces the data; it's generator work.

**Phase 3 — a second provider.** GitLab CI or CircleCI. This is the real test of
whether the generator's abstraction holds: if `build_context` needs reshaping to
fit a second provider, better to learn that at two than at five. Depends on
Phase 2 only if you want workspace support in both. Moderate.

**Phase 4 — deploy steps.** `deploy` is detected (Vercel, Netlify, Fly, …) and
then never used. A deploy job gated on the default branch is the natural payoff.
Depends on Phase 3 for the provider abstraction. Larger, and it's the first
feature that needs secrets handling — worth designing before building.

**Phase 5 — config file.** A `piper.toml` for overriding detection when it
guesses wrong. Worth deferring until real usage shows *what* people need to
override; guessing now means building the wrong knobs. (This is also where
`tomli-w` would finally earn its dependency.)
