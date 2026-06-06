# Claude Code Project Context

This file provides essential context for Claude Code when working on this project.

## Project Documentation

The following files contain critical project information:

- @docs/prd.md - Product Requirements Document with goals, features, and technical requirements
- @docs/as-built-project-guide.md - Combined discovery and architecture guide. **Read this before implementing anything new** to find what already exists, understand architecture decisions, and follow established patterns.

## Development Environment

**Platform**: macOS
When suggesting commands, scripts, or configuration options, **ALWAYS** use macOS-compatible options. Avoid Linux-specific flags or Windows-specific commands.

### Definition of Done

Implementation is complete when:

- All acceptance criteria are met
- All tests pass - no exceptions, no skipped tests
- Minimum 80% code coverage on every file touched in the commit
- No formatter, linter, or type checker issues

### Quality Gate

The quality gate runs as a **git pre-commit hook** (`.git/hooks/pre-commit`), not after every Claude edit:

- Ruff formats and lints staged Python files (formatting fixes are auto-restaged)
- detect-secrets scans for new secrets against `.secrets.baseline`
- Commit is blocked on lint errors or new secrets

Run `pytest tests/ -v` and the coverage report manually — they are intentionally not in the pre-commit hook (too slow to run on every commit). Treat them as the last check before opening a PR.

### As-Built Project Guide Maintenance

**NEVER commit to git without first updating `docs/as-built-project-guide.md`** to reflect any changes:

- New systems, modules, or components added
- Systems removed or relocated
- New execution contexts or API endpoints
- New settings categories or key functions

This is a **hard requirement**. If you added, removed, or modified any server systems, components, API endpoints, or settings, the as-built project guide **MUST** be updated in the same commit.
**Before updating the as-built project guide**, read `docs/as-built-project-guide.md` for guidance on what to include and how to maintain consistency.
The as-built project guide is the primary discovery document for finding existing functionality. Keeping it current prevents duplicate implementations and helps integrate with existing patterns.

## Coding Guidelines

### Code Style

- **ALWAYS** use tabs for indentation, not spaces. Ruff is configured to enforce this.
- Focus above all on code readability. Code is read much more than written.
- **NEVER** use leet code or clever code solutions. Clean readable code is the goal.
- **NEVER** comment on what code is doing, instead comment why. If you have to comment what that indicates poor readability.
- **ALWAYS** minimize function/file length and nesting as much as possible.
- **ALWAYS** use descriptive names. There is no advantage to using "i" over "index".
- **ALWAYS** include units in names such as "timeoutSeconds" or "distanceMeters". This avoids unit confusion.
- **NEVER** use magic numbers.
- **ALWAYS** use documentation comments on functions and classes.
- **ALWAYS** keep comments current.
- **NEVER** change "AI NOTE:" comments. These comments are explicitly intended to guide AI agents where they often make mistakes.
- **ALWAYS** use early returns to minimize nesting.

### Assertions

- **ALWAYS** validate function inputs to ensure they meet requirements
- **ALWAYS** validate function outputs when processing could fail to produce correct output
- **ALWAYS** use descriptive assertion messages that explain what was expected

### Error Handling

- **ALWAYS** catch and handle recoverable errors at the appropriate level
- **NEVER** catch errors only to re-throw them to the next level
- Internal functions and classes should throw unrecoverable errors and let them bubble up
- UI components should catch all errors and display them to the user

## Working Practices

### Permissions

- **PREFER "this session only"** when granting tool permissions at runtime. Do not add permanent allow rules to settings files unless the user explicitly requests it.

### Branch Strategy

- **ALWAYS create a new branch** before starting work on any new feature, fix, or task. Never commit to `main` or the current branch without asking first.
- Use descriptive branch names that reflect the work (e.g., `fix/auth-timeout`, `feat/user-profile`).

### Pull Requests

- **ALWAYS keep PRs focused on a single function, feature, or fix.** Never bundle unrelated changes in one PR.
- When a task grows beyond a single concern, pause and tell the user before continuing.
- Remind the user to split broad changes into separate PRs when appropriate.

## Time Handling

### Storage and Internal Processing

- **Use UTC for most internal storage** - Timestamps in the database, logs, and internal APIs should use UTC
- **Use floating time for location-independent events** - Some times have no timezone (e.g., "8:00 AM" means "8:00 AM wherever the user is at that moment"). These should be stored without timezone information.

### LLM Communication

- **ALWAYS normalize times to the user's timezone when presenting to the LLM**
- LLMs do not handle timezone conversions reliably. All times in system prompts, tool outputs, and context should be in the user's local timezone.
- Include the timezone explicitly when relevant (e.g., "3:00 PM PST" not just "3:00 PM")
