---
name: create-good-commit
description: Inspect, prepare, and create focused Git commits using Conventional Commits and the user's preferred language. Use when asked to draft, review, split, amend, fix up, or create commits; do not use for pushing or unrelated Git operations.
---

# Create Good Commit

Create a commit that represents one coherent change, contains exactly the intended files or hunks, passes relevant checks, and leaves an understandable historical record.

## Authorization and Safety

- Distinguish advice from execution. If the user asks only for a review or message, do not stage files or create a commit. A direct request to commit authorizes the necessary staging and `git commit`, but not a push.
- Preserve staged, unstaged, and untracked work. Never discard changes, overwrite the index, or include unrelated work. Do not use `git add -A` or `git add .` without first proving that every affected path belongs in the commit.
- Do not change Git identity or repository configuration. Do not invent authors, co-authors, issue references, sign-offs, or signatures.
- Never bypass hooks with `--no-verify` by default. If a hook fails, report the failure and address its cause; bypass it only when the user explicitly directs that specific action after seeing the risk.
- Treat amend, fixup, rebase, reset, and other history/index rewrites as separate operations. Use them only when requested, after checking whether the affected commits may already be shared.
- Never push as part of this skill.

## Workflow

### 1. Discover Constraints

Read applicable repository instructions such as `AGENTS.md`, `CONTRIBUTING*`, commit templates, commitlint configuration, release configuration, and recent commit history. Inspect history for the paths being changed when useful.

Use this precedence:

1. The user's explicit requirements.
2. Repository-enforced constraints.
3. This skill's Conventional Commits defaults.

Always produce a Conventional Commit. If a repository rule is incompatible with Conventional Commits, explain the conflict instead of silently violating either rule.

### 2. Inspect the Actual State

Use `git status`, the staged diff, and the unstaged diff to establish what exists and what would be committed. Adapt commands for an initial repository with no `HEAD`.

At minimum, inspect:

- the current branch and concise status;
- the complete staged diff and its stat;
- unstaged changes and untracked path names;
- `git diff --cached --check` before committing.

Do not infer commit contents from the conversation or a file list alone. Inspect the diff.

### 3. Choose an Atomic Boundary

Group changes by purpose, not merely by file location. Implementation, its tests, and directly required documentation can form one commit. Unrelated refactors, formatting, dependency changes, generated artifacts, or opportunistic fixes usually belong in separate commits.

If the candidate contains multiple independent purposes, propose or perform a safe split only within the user's authorization. Do not disturb pre-existing staged changes to manufacture a split without making the impact clear.

### 4. Validate the Candidate

Run the smallest relevant repository checks that give meaningful confidence, such as focused tests, lint, type checking, build steps, and whitespace checks. Expand validation when the change is broad or repository instructions require it.

Report checks exactly as run. Do not claim that skipped checks passed. If a required or relevant check fails, stop before committing unless the user explicitly chooses to proceed after the failure is explained.

### 5. Construct the Message

Use this form:

```text
<type>[optional scope][!]: <description>

[optional body]

[optional footer(s)]
```

Select the message language in this order:

1. A language explicitly requested for the commit.
2. The primary language of the user's current request.
3. The primary language of the current conversation.
4. English when none can be determined reliably.

Apply that language to the description, body, and explanatory footer values. Keep Conventional Commits tokens, standard trailer tokens, code identifiers, paths, commands, and proper nouns in their canonical form. In particular, keep `type` in lowercase English and `BREAKING CHANGE` uppercase.

Use a concise, action-oriented description natural to the selected language. Add a body when the motivation, behavior, risk, or non-obvious tradeoff would otherwise be lost. Describe why and impact rather than narrating the diff.

For ambiguous types or scopes, breaking changes, trailers, reverts, or multilingual examples, read [references/conventional-commits.md](references/conventional-commits.md).

### 6. Commit Only When Authorized

When execution is authorized and the scope is clear, stage only the intended paths or hunks and create the commit without unsafe shell interpolation. Let configured hooks and signing policy run normally.

If the user requested only a draft, return the proposed message and the evidence used to derive it without changing repository state.

### 7. Verify the Result

After creating a commit, inspect the resulting commit and current worktree. Report:

- commit hash and subject;
- committed scope;
- checks run and their outcomes;
- remaining staged, unstaged, or untracked changes.

Do not describe the operation as complete if the resulting commit contains unintended changes or required checks remain unresolved.
