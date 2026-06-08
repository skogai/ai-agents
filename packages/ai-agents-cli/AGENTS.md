# AGENTS.md

Cross-platform harness spec for AI coding agents.

This file describes the agent team vendored into `.claude/`.
Compatible with Claude Code, GitHub Copilot CLI, and other harness-aware tools.

## What is here

- `.claude/commands/` — slash commands for lifecycle phases
- `.claude/agents/` — specialized agent definitions
- `.claude/skills/` — domain knowledge and workflows

## Interop

This is a harness spec with interop across multiple AI coding tools.
Any tool that reads `.claude/` or `AGENTS.md` can consume it.
The content is portable across Claude Code, Copilot, and Codex runtimes.

## Getting started

Open this folder in Claude Code and run `/spec` to start.
