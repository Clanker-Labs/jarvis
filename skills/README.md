# Skills

Each folder here is a skill Jarvis can pick up. Changes are hot-reloaded; you can also upload, edit,
enable and disable skills from the admin panel (Skills page).

```
skills/<name>/
  SKILL.md          # required: YAML frontmatter + instructions
  manifest.yaml     # optional: side effects of scripts, required secrets
  scripts/          # optional: .py / .sh / .js — each becomes a tool skill__<name>__<script>
  *.md, data files  # optional: extra material the instructions can tell Jarvis to read
```

`SKILL.md` frontmatter:

```yaml
---
name: morning-briefing          # must equal the folder name (lowercase, digits, dashes)
description: One line saying what the skill does — this is all Jarvis sees until it loads it.
triggers: [good morning, brief me]   # optional hints for when to use it
---
```

Only the name, description and triggers go into the prompt. The full instructions are loaded when
Jarvis decides to use the skill (`skill_load` tool).

**Scripts are irreversible by default** — Jarvis asks for spoken confirmation before running one —
unless `manifest.yaml` declares otherwise:

```yaml
id: dice
name: Dice
type: skill
required_secrets: []          # passed to scripts as environment variables
tools:
  - {name: roll, side_effect: none, description: Roll a die with N sides.}
```

Scripts run with a timeout, a minimal environment, `$SKILL_DIR` set to the skill folder and a private
working directory under `data/skill-runs/<skill>/`.
