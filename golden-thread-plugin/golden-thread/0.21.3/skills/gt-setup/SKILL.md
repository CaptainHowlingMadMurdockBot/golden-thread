---
name: gt-setup
description: "Finish installing Golden Thread from inside Claude Code, with no terminal: choose which modules to install (everything, a few, or core only), the security level and the vault, and have the installer run for you. Use when the user says: finish the install, finish setting up gt, install all the modules, install everything, add a module, remove a module, gt is not set up, I just installed gt from the marketplace. To create a vault or wire a project only, use /gt:gt-init."
plain:
  - cmd: "/gt:gt-setup"
    does: "Finishes the Golden Thread install from the prompt: modules, security level, vault."
    how: "Asks whether to install everything, pick modules or keep core only, then the security questions and where your vault lives, and runs the installer with your answers. You never open a terminal or install a plugin one at a time."
    when: "Right after /plugin install gt-all@golden-thread-plugin (or gt alone), or any time you want to add or remove modules."
    next: "/gt:gt-doctor"
    hear: "finish the install|install everything|install all the modules|add a module|gt is not set up"
model_intent: balanced
---

# Golden Thread Setup

**Under gt sandbox mode** (the session-start line says SANDBOX MODE; `gt_settings.py get sandbox_mode` prints `on`) Claude's shell cannot write `~/.claude`, so setup cannot run from here. Every step below that runs the installer is then a terminal step: ask the questions as usual, then give the user the exact command to run from a terminal.

| Shell step | Under gt sandbox mode |
|---|---|
| `gt_setup.py …` | **terminal**: it runs the installer, which writes `~/.claude` |


Claude Code installs plugins; it cannot wire hooks, install gt's scripts, record a security level or create a vault. `install.sh` does those, and until 0.21.3 only a terminal could run it. This skill is the prompt route to the same installer: you ask the questions here, then run it with the answers. It is the machine-level half of
`/plugin marketplace add shaven/golden-thread` then `/plugin install gt-all@golden-thread-plugin`, and it is safe to re-run (to add or remove modules, or to change a choice).

Everything is driven by `gt_setup.py` in this plugin's `scripts/` folder. `<base_dir>` is the path in the `Base directory for this skill:` header; resolve `<base_dir>/../../scripts/gt_setup.py` to an absolute path before running it.

## Step 1 — Look at the machine

```bash
python3 <base_dir>/../../scripts/gt_setup.py plan --json
```

Read `set_up` (installer already run?), `installer` (null means the checkout holding `install.sh` could not be found: tell the user, ask for the `golden-thread-plugin` folder of their checkout, and run everything below with `GT_INSTALLER=<folder>/install.sh` in front), `modules` (name, summary, default, `in_bundle`), `module_question` and `vault`. If `set_up` is true, say so and ask whether they want to change modules or choices; otherwise carry on.

## Step 2 — Ask, one question at a time, with AskUserQuestion

1. **Modules** (`module_question`): "Install everything (recommended)", "Choose modules", "Core only". Show each option's `explain` line. Never skip this question or answer it for them.
   - Choose modules: ask which of `modules` they want, in AskUserQuestion's multi-select form (it takes up to four options per question, so group them: put the ones with `in_bundle` and `default: on` first and ask in as many questions as needed; the summary of each module is the option's description). `farm` and `lotr` are not in the everything bundle; offer them only here, and say that `lotr` is a gateway to GitHub/Jira/Microsoft 365 with its own setup.
2. **The installer's own questions.** Run `bash <installer> --questions --json` and ask each one exactly as `/gt:gt-init` does in its "Install choices" section: the security level (`lockdown`) first, then `sandbox_mode`, then the rest, in the listed order, showing each question's `plain_text` and options, the recommended and current value marked, never defaulting a security question for them. Skip a question whose `applies` is false. Do NOT ask the `pane` question again if they chose modules above: choosing the pane there answered it.
3. **The vault.** If `vault.configured` is true, say which vault will be used and ask whether to keep it. Otherwise ask: "Where should your vault live?" offering `default_vault`, "a vault I already have" (they type the path), or "no vault yet" (`--no-vault`; the Core-rule enforcement hooks are wired against a vault, so say they stay unwired until one exists).

## Step 3 — One review screen, then run

Show every answer in one message (modules, each installer answer marked "chosen by you" or "kept", the vault) and ask whether to install or to change one. Nothing runs before they confirm.

Then run the installer through the helper, which passes exactly these answers and nothing else, and is not at a terminal:

```bash
python3 <base_dir>/../../scripts/gt_setup.py run --modules <everything|choose|core> [--with NAME ...] --set lockdown=<level> --set sandbox_mode=<off|on> [--set KEY=VALUE ...] (--vault "<path>" | --no-vault)
```

`--dry-run` prints the exact installer command without running it; use it if they ask what will happen. Show the installer's own lines about each applied choice. On **exit 5** a security question had no answer: ask it, then run again (never add `--accept-defaults` unless they say so in those words).

## Step 4 — Finish

Tell them to **restart Claude Code** so the wired hooks and the new plugins load (the installer says so too), then run `/gt:gt-doctor` in the new session. If they chose "everything", nothing else needs installing; if they later want `lotr` or `farm`, `/gt:gt-setup` again (choose modules).

## What this does not do

- It never changes a setting without asking, and never answers a security question.
- It does not install plugins by itself: that is `/plugin install`. `gt-all` pulls every module in as one install; `gt` alone is the minimal one. Running `/gt:gt-setup` with "Choose modules" or "Core only" over a `gt-all` install turns `gt-all` itself off (it would force the modules back on), and says so.
- Under gt sandbox mode this is a **terminal** step (it writes `~/.claude`): give the user `bash <installer> --all-modules --vault <path>` (or the flags above) to run themselves.
