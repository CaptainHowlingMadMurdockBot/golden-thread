---
name: gt-close
description: "Close this session's work — the opposite of /gt:gt-open: write back what the session learned (gt-work) and release the session's claim on the vault. To complete or archive a project, a task or a handoff, use /gt:gt-complete. Use when the user says: close this session, close out the session, I'm done for now, wrap up this session, close the project I opened."
plain:
  - cmd: "/gt:gt-close"
    does: "Saves what this session learned and closes it."
    how: "Writes the session's findings into the project's notes through the safe write queue, then lets go of the session's claim on the vault."
    detail: "runs the gt-work write-back (research.md, decisions.md, design.md) through gt_write_queue, then gt_session.py release."
    when: "You are finished for now and want nothing lost."
    next: "/gt:gt-list, /gt:gt-complete"
    hear: "close this session|close out the session|im done for now|wrap up this session"
model_intent: balanced
---

# Golden Thread — Close

**Under gt sandbox mode** (the session-start line says SANDBOX MODE; `gt_settings.py get sandbox_mode` prints `on`) Claude's shell and file tools cannot write the vault, and by default cannot read it. Every vault step below then takes the route in this table instead: a gt-vault MCP tool runs the same script, with the same checks, outside the sandbox. With sandbox mode off (the default) nothing changes: the shell steps run as written.

| Shell step | Under gt sandbox mode |
|---|---|
| `gt_session.py release` | skipped: a sandboxed session holds no registration of its own to release (the server stamps its own session on every write); say so and stop after the write-back |

`/gt:gt-open` loads a project into a session; `/gt:gt-close` ends that session's work on it. It
does not complete, archive or retire anything: that is `/gt:gt-complete` (since 0.21.0 — before
it, `/gt:gt-close` did what `/gt:gt-complete` now does).

The write-back takes the routes `/gt:gt-work` lists.

## Steps

1. **Write back.** Read `<base_dir>/../gt-work/SKILL.md` — `<base_dir>` is the path in this skill's
   `Base directory for this skill:` header — and follow it from its first step to its last, with the
   project this session opened (`/gt:gt-open <slug>`) as the project. Do not restate or shorten its
   steps here: that file is the procedure, and a change to it is a change to this command. If it
   finds nothing worth keeping, say that in one line.
2. **Release the session.** Unless the write-back has already released this session's registration
   (it says so when it has), run
   ```bash
   python3 "<vault>/Projects/golden-thread/tools/gt_session.py" release --session "<session id>" --vault "<vault>"
   ```
   (`<vault>` is `$GT_VAULT`, else `vault_path` from `~/.claude/vault-config.json`; Core rule 2: name
   it.) Echo what it printed. It refuses to clear claims another live process holds; relay that
   and stop, never `--force` it.
3. **Say what is left.** One line: the project is still open (not completed); to finish it, run
   `/gt:gt-complete project <slug>`. If the session has no project open, ask which work to write
   back before running `/gt:gt-work`; never guess.

## Rules

- **Close is not complete.** Nothing is archived, shelved, moved or marked done here.
- **Follow gt-work by reference.** Do not copy its steps into this file.
- Every vault write is a tool's, through the write queue. No Write/Edit on a vault path.
