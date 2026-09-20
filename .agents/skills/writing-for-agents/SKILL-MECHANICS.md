# Codex skill mechanics

Read [SKILL.md](SKILL.md) for context pointers, progressive disclosure and pruning.

A Codex skill requires YAML frontmatter with `name` and a meaningful `description`.
Keep descriptions focused on the actual trigger. Preserve `agents/openai.yaml`
UI metadata. Normal implicit discovery is the default; an explicitly requested
explicit-only skill uses `policy.allow_implicit_invocation: false` in that file.
Keep the description even for explicit-only use. Do not introduce another
agent product's invocation flags or configuration.

Use `$skill-name` for explicit invocation. To apply a related installed skill,
read its SKILL.md through the available filesystem/skill capability; do not assume
a tool literally named Skill exists. Plain local references are suitable for
shared material without creating more independently discoverable skills.

Split skills only when distinct triggers or materially different work justify it.
Do not change invocation policy merely because a skill performs sensitive work;
authorization belongs at the concrete action boundary.
