.PHONY: setup test help

help:
	@echo "Available targets:"
	@echo "  make setup   — configure git hooks and verify dev tools"
	@echo "  make test    — run pytest with coverage"

setup:
	@echo "Configuring git to use .githooks/..."
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "✅ Done. Pre-commit checks are now active."

test:
	.venv/bin/pytest tests/ -v
