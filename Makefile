# aloop — developer & release entrypoint.
# Run `make` (no target) for help.

SHELL        := bash
.DEFAULT_GOAL := help

PLUGIN       := aloop
MARKET       := aloop-marketplace
PLUGIN_DIR   := plugin
CTX          := plugin/tools/ctx-symbols
MDDB         := plugin/tools/md-db
BIN_DIR      ?= $(HOME)/.local/bin
REPO         := $(CURDIR)
VERSION      := $(shell python3 -c "import json;print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])")
TAG          := v$(VERSION)
PKG_DIR      := package
PKG          := $(PKG_DIR)/aloop-$(TAG).tar.gz

# Colors
C := \033[36m
B := \033[1m
R := \033[0m

.PHONY: help
help: ## Show this help
	@printf "$(B)aloop$(R)  v$(VERSION)\n\n"
	@printf "Usage: make $(C)<target>$(R)\n\n"
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | sort | awk 'BEGIN{FS=":.*?## "}{printf "  $(C)%-16s$(R) %s\n",$$1,$$2}'

# ── Build / install ────────────────────────────────────────────────
.PHONY: build
build: ## Build ctx-symbols + md-db (release)
	cd $(CTX) && cargo build --release
	cd $(MDDB) && cargo build --release

.PHONY: install
install: ## Build + install ctx-symbols and md-db to BIN_DIR
	./plugin/tools/install.sh "$(BIN_DIR)"

.PHONY: link
link: ## Register THIS repo as a local marketplace and install the plugin (dev)
	@command -v claude >/dev/null || { echo "claude CLI not found — install: curl -fsSL https://claude.ai/install.sh | bash -s stable"; exit 1; }
	claude plugin marketplace add "$(REPO)"
	claude plugin install "$(PLUGIN)@$(MARKET)"

.PHONY: unlink
unlink: ## Uninstall the plugin and remove the local marketplace
	-claude plugin uninstall "$(PLUGIN)@$(MARKET)"
	-claude plugin marketplace remove "$(MARKET)"

# ── Format / lint / test ───────────────────────────────────────────
.PHONY: fmt
fmt: ## Format ctx-symbols
	cd $(CTX) && cargo fmt

.PHONY: fmt-check
fmt-check: ## Check formatting (no writes)
	cd $(CTX) && cargo fmt --all -- --check

.PHONY: clippy
clippy: ## Clippy with warnings-as-errors
	cd $(CTX) && cargo clippy --all-targets -- -D warnings

.PHONY: shellcheck
shellcheck: ## Lint gate scripts (skipped with a warning if shellcheck is absent)
	@if ! command -v shellcheck >/dev/null; then echo "WARN: shellcheck not installed; skipping (CI enforces it)"; else \
	  shellcheck -S error -e SC1091 plugin/bin/_gatelib.sh plugin/bin/_failclosed.sh $$(grep -l "^#!/usr/bin/env bash" plugin/bin/gate-*) plugin/bin/log-execution \
	       plugin/bin/selfcheck plugin/bin/transcripts plugin/bin/worktree-create plugin/bin/worktree-remove \
	       plugin/bin/worktree-hygiene plugin/bin/ensure-tools plugin/bin/md-db plugin/bin/ctx-symbols \
	       plugin/bin/session-start plugin/bin/_paths.sh plugin/bin/aloop plugin/bin/aladdin plugin/bin/agentic plugin/bin/bind plugin/bin/task-worktree; fi

.PHONY: pycheck
pycheck: ## Byte-compile the Python hook scripts
	@python3 -m py_compile plugin/bin/budget plugin/bin/stats plugin/bin/aloop-init plugin/bin/gate-commit-author plugin/bin/_paths.py \
	    plugin/bin/_usage.py plugin/bin/_events.py plugin/bin/events plugin/bin/intent plugin/bin/lessons plugin/bin/ids plugin/bin/targets plugin/bin/impact plugin/bin/timebox plugin/bin/sweep plugin/bin/capabilities plugin/bin/models plugin/bin/_routes.py plugin/bin/host plugin/bin/_host.py plugin/bin/config plugin/bin/_config.py plugin/bin/layout plugin/bin/_lsp.py scripts/test/fixtures/fake-lsp.py plugin/bin/_timebox.py plugin/bin/host-adapter plugin/bin/install \
	  && rm -rf plugin/bin/__pycache__ && echo "python ok"

.PHONY: test-hosts
test-hosts: ## Budget/worktree regression + Codex hooks emulation + OpenCode plugin end-to-end (no network, no tokens)
	@command -v node >/dev/null && node --check plugin/hosts/opencode/aloop.js
	python3 scripts/test/budget-worktree.test.py
	python3 scripts/test/budget-soft-telemetry.test.py
	python3 scripts/test/budget-hard-stop.test.py
	python3 scripts/test/budget-meter.test.py
	python3 scripts/test/budget-roles.test.py
	python3 scripts/test/budget-classify.test.py
	python3 scripts/test/sprint-admission.test.py
	python3 scripts/test/planner-feedback.test.py
	python3 scripts/test/events.test.py
	python3 scripts/test/handoff-resume.test.py
	python3 scripts/test/metrics.test.py
	python3 scripts/test/intent-contract.test.py
	python3 scripts/test/lesson-candidates.test.py
	python3 scripts/test/ids-integrity.test.py
	python3 scripts/test/semantic-targets.test.py
	python3 scripts/test/commit-trace.test.py
	python3 scripts/test/impact-provider.test.py
	python3 scripts/test/timebox-auto.test.py
	python3 scripts/test/orchestrator-sweep.test.py
	python3 scripts/test/lsp-adapter.test.py
	python3 scripts/test/context-boundary.test.py
	python3 scripts/test/model-routes.test.py
	python3 scripts/test/host-adapter.test.py
	python3 scripts/test/portable-config.test.py
	python3 scripts/test/host-swap.test.py
	python3 scripts/test/aladdin-cli.test.py
	python3 scripts/test/site.test.py
	python3 scripts/test/readme.test.py
	python3 scripts/test/layout.test.py
	plugin/bin/layout check
	plugin/bin/host check
	python3 scripts/test/targets-scope.test.py
	python3 scripts/test/plan-render.test.py
	python3 scripts/check-tracking.py
	python3 scripts/test/codex-hooks.test.py
	python3 scripts/test/rename-compat.test.py
	python3 scripts/test/supervisor-scope.test.py
	python3 scripts/test/planner-stuck.test.py
	python3 scripts/test/hook-matchers.test.py
	python3 scripts/test/dispatch.test.py
	python3 scripts/test/log-execution.test.py
	python3 scripts/test/ensure-tools.test.py
	python3 scripts/test/fail-closed.test.py
	python3 scripts/test/intent-trigger.test.py
	python3 scripts/test/evidence.test.py
	@if command -v node >/dev/null; then node scripts/test/opencode-plugin.test.mjs; else echo "node not found — skipped OpenCode test (CI runs it)"; fi

.PHONY: conformance
conformance: ## ROADMAP §6 conformance fixtures on today's gates (needs cargo; no network, no tokens)
	python3 scripts/conformance/claude-code.test.py

.PHONY: e2e-live
e2e-live: ## Live end-to-end run: one-line intent -> planned, tested, merged code (YES=1; spends dollars)
	@test -n "$(YES)" || { echo "spends tokens: make e2e-live YES=1"; exit 2; }
	scripts/smoke/e2e-live.sh --yes $(if $(INTENT),--intent $(INTENT))

.PHONY: smoke-live
smoke-live: ## Live Claude Code session: hooks fire, supervisor write blocked, transcripts + ledger (YES=1; spends ~$0.005)
	@test -n "$(YES)" || { echo "spends tokens: make smoke-live YES=1"; exit 2; }
	scripts/smoke/live-claude.sh --yes

.PHONY: json
json: ## Validate plugin.json + marketplace.json
	@python3 -c "import json;json.load(open('plugin/.claude-plugin/plugin.json'));json.load(open('.claude-plugin/marketplace.json'));print('JSON ok')"

.PHONY: lint
lint: clippy shellcheck pycheck json ## Run all linters

.PHONY: test
test: ## Run ctx-symbols + md-db unit tests
	cd $(CTX) && cargo test --all-targets
	cd $(MDDB) && cargo test --release

.PHONY: validate
validate: json ## claude plugin validate --strict
	@command -v claude >/dev/null || { echo "claude CLI not found — install: curl -fsSL https://claude.ai/install.sh | bash -s stable"; exit 1; }
	claude plugin validate ./plugin --strict

.PHONY: ci
ci: fmt-check lint test eval-validate test-hosts conformance ## Run everything CI runs (except the live claude install)

# ── Evals ──────────────────────────────────────────────────────────
.PHONY: eval-validate
eval-validate: ## Validate every eval suite's JSON (no tokens)
	@python3 scripts/eval/validate-suites.py

.PHONY: eval
eval: ## Run an eval suite: make eval SUITE=<evals.json> [YES=1] (YES spends tokens)
	@test -n "$(SUITE)" || { echo "set SUITE=<path to evals.json> (see plugin/evals/ or the skill's evals/)"; exit 2; }
	@./scripts/eval/run-evals.sh --suite "$(SUITE)" $(if $(YES),--yes,) $(if $(ITER),--iteration $(ITER),)

# ── Release / publish ──────────────────────────────────────────────
.PHONY: version
version: ## Print the current version
	@echo "$(VERSION)"

.PHONY: version-check
version-check: ## Assert plugin.json, Cargo.toml, and CHANGELOG agree
	@python3 scripts/version-check.py

.PHONY: release
release: ci validate version-check ## Verify, then create annotated git tag vX.Y.Z
	@git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo — run: git init && git add -A && git commit -m init"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "working tree dirty — commit first"; exit 1; }
	@git rev-parse "$(TAG)" >/dev/null 2>&1 && { echo "tag $(TAG) already exists"; exit 1; } || true
	git tag -a "$(TAG)" -m "aloop $(TAG)"
	@echo "Tagged $(TAG). Run 'make publish' to push."

.PHONY: publish
publish: ## Push the current branch + tags to origin (run after `make release`)
	@git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo"; exit 1; }
	@git remote get-url origin >/dev/null 2>&1 || { echo "no 'origin' remote — git remote add origin <url>"; exit 1; }
	git push origin HEAD
	git push origin --tags
	@echo "Published. Users: /plugin marketplace add <repo-url> && /plugin install $(PLUGIN)@$(MARKET)"

.PHONY: package
package: ## Bundle the release into package/ and publish it as the GitHub release for $(TAG)
	@command -v gh >/dev/null || { echo "gh CLI not found — https://cli.github.com"; exit 1; }
	@git rev-parse "$(TAG)" >/dev/null 2>&1 || { echo "tag $(TAG) missing — run 'make release && make publish' first"; exit 1; }
	@mkdir -p $(PKG_DIR)
	@rm -f "$(PKG)" "$(PKG).sha256" "$(PKG_DIR)/notes-$(TAG).md"
	tar --no-xattrs \
	  --exclude='.git' --exclude='target' --exclude='.agentic' --exclude='.transcripts' \
	  --exclude='.DS_Store' --exclude='node_modules' --exclude='$(PKG_DIR)' \
	  -czf "$(PKG)" .claude-plugin plugin README.md CHANGELOG.md LICENSE
	@cd $(PKG_DIR) && shasum -a 256 "$(notdir $(PKG))" > "$(notdir $(PKG)).sha256"
	@awk -v v='$(VERSION)' 'BEGIN{p=0} $$0 ~ "^## \\["v"\\]"{p=1;next} /^## \[/{if(p)exit} p' CHANGELOG.md > "$(PKG_DIR)/notes-$(TAG).md"
	@test -s "$(PKG_DIR)/notes-$(TAG).md" || echo "aloop $(TAG)" > "$(PKG_DIR)/notes-$(TAG).md"
	@echo "Built $(PKG)"
	@if gh release view "$(TAG)" >/dev/null 2>&1; then \
	  gh release upload "$(TAG)" "$(PKG)" "$(PKG).sha256" --clobber; \
	  gh release edit "$(TAG)" --latest --notes-file "$(PKG_DIR)/notes-$(TAG).md"; \
	else \
	  gh release create "$(TAG)" "$(PKG)" "$(PKG).sha256" --title "aloop $(TAG)" \
	    --notes-file "$(PKG_DIR)/notes-$(TAG).md" --latest; \
	fi
	@echo "Published GitHub release $(TAG) with $(notdir $(PKG))."

# ── Hooks / housekeeping ───────────────────────────────────────────
.PHONY: hooks
hooks: ## Install the tracked git hooks (sets core.hooksPath)
	git config core.hooksPath .githooks
	chmod +x .githooks/* 2>/dev/null || true
	@echo "git hooks installed (.githooks): pre-commit, pre-push"

.PHONY: smoke
smoke: build ## Quick offline gate smoke test (structural gate on a tmp tree)
	@export PATH="$(BIN_DIR):$$PATH"; \
	  cp "$(CTX)/target/release/ctx-symbols" "$(BIN_DIR)/ctx-symbols" 2>/dev/null || true; \
	  T=$$(mktemp -d); printf 'pub fn a(){let x=1;}\npub fn caller(){a();}\n' > "$$T/a.rs"; \
	  REPO_DIR="$$T" CLAUDE_PLUGIN_ROOT="$(CURDIR)/plugin" plugin/bin/gate-structural-integrity; \
	  echo "structural gate exit=$$?"; rm -rf "$$T"

.PHONY: clean
clean: ## Remove build artifacts
	cd $(CTX) && cargo clean
