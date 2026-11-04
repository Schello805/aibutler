# AI Butler development checks

After every completed code change, run `sh scripts/check.sh` before reporting the
change as complete or committing it. This runs backend tests (including the fixed
question catalogue), frontend interaction tests, and synthetic evidence-quality
checks. Add a regression case when fixing a conversation or retrieval bug.

Use only synthetic documents and mocked service responses in automatic tests.
Do not read private application data, call paid LLMs, send notifications, or actuate
real smart-home devices as part of this gate. Optional live model evaluation via
`scripts/evaluate_quality.py --llm` is separate and is not a substitute for the gate.

The local pre-commit hook uses the same gate; enable it for a new checkout with
`git config core.hooksPath .githooks`. GitHub Actions runs it on every push and pull
request. Do not bypass a failing check to publish a change; report the failure.
