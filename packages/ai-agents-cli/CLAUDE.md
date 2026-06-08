<!-- ai-agents:begin -->
# ai-agents Harness

Vendored by [@rjmurillo/ai-agents](https://github.com/rjmurillo/ai-agents).

## Skill Routing

When your request matches an available skill, invoke it using the Skill tool
as your FIRST action. Skills provide specialized workflows.

Key routing:
- Bugs, errors -> /analyze
- PRs, issues -> /github
- Define requirements -> /spec
- Plan work -> /plan
- Implement -> /build
- Test -> /test
- Review code -> /review
- Ship, deploy -> /ship

## Memory Interface

| Scenario | Tool |
|----------|------|
| Quick search | /memory-search |
| Deep exploration | context-retrieval agent |
| Direct MCP | mcp__serena__read_memory |

## Agents

Use the Task tool with specialized agents:
- orchestrator: multi-step coordination
- analyst: research and investigation
- architect: design and ADRs
- implementer: code and tests
- critic: plan validation
- qa: testing and verification
<!-- ai-agents:end -->
