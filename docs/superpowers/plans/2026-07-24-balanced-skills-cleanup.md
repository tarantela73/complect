# Balanced Skills Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the umbrella Superpowers export with a curated nine-skill set while preserving the source repository and a recoverable rollback path.

**Architecture:** `~/.superpowers` remains the canonical git checkout. The broad `~/.agents/skills/superpowers` symlink is moved outside the discovery directory as a backup, and nine direct skill symlinks replace it. System Codex skills and caches remain untouched.

**Tech Stack:** Git, POSIX shell, symbolic links, Codex skill discovery

---

### Task 1: Preflight and update the canonical source

**Files:**
- Verify: `/home/sever/.superpowers/skills/*/SKILL.md`
- Preserve: `/home/sever/.superpowers/update.sh`
- Modify: tracked files under `/home/sever/.superpowers/` through `git pull`

- [ ] **Step 1: Verify the current export and repository state**

Run:

```bash
test "$(readlink /home/sever/.agents/skills/superpowers)" = "/home/sever/.superpowers/skills"
git -C /home/sever/.superpowers status --short --branch
test -f /home/sever/.superpowers/update.sh
```

Expected: the first and third commands exit 0; Git reports only the known
untracked `update.sh` and that `main` is behind upstream.

- [ ] **Step 2: Verify every selected source skill exists**

Run:

```bash
for skill_name in \
  systematic-debugging \
  test-driven-development \
  verification-before-completion \
  receiving-code-review \
  requesting-code-review \
  writing-plans \
  executing-plans \
  finishing-a-development-branch \
  using-git-worktrees
do
  test -f "/home/sever/.superpowers/skills/${skill_name}/SKILL.md" || exit 1
done
```

Expected: exit 0 with no output.

- [ ] **Step 3: Update the source without rewriting local history**

Run:

```bash
git -C /home/sever/.superpowers pull --ff-only
```

Expected: exit 0 with a fast-forward update. The untracked `update.sh` remains.

- [ ] **Step 4: Verify the updated source**

Run:

```bash
git -C /home/sever/.superpowers status --short --branch
test -f /home/sever/.superpowers/update.sh
```

Expected: branch is aligned with upstream; `?? update.sh` remains; the file
check exits 0.

### Task 2: Replace the umbrella export with curated links

**Files:**
- Move: `/home/sever/.agents/skills/superpowers`
- Create: `/home/sever/.agents/superpowers-skills-backup`
- Create: nine symlinks under `/home/sever/.agents/skills/`

- [ ] **Step 1: Ensure no backup or target paths would be overwritten**

Run:

```bash
test ! -e /home/sever/.agents/superpowers-skills-backup
for skill_name in \
  systematic-debugging \
  test-driven-development \
  verification-before-completion \
  receiving-code-review \
  requesting-code-review \
  writing-plans \
  executing-plans \
  finishing-a-development-branch \
  using-git-worktrees
do
  test ! -e "/home/sever/.agents/skills/${skill_name}" || exit 1
done
```

Expected: exit 0 with no output. Any failure stops the task before mutation.

- [ ] **Step 2: Move the umbrella symlink to the rollback location**

Run:

```bash
mv /home/sever/.agents/skills/superpowers /home/sever/.agents/superpowers-skills-backup
```

Expected: the original symlink is absent from the discovery directory and the
backup resolves to `/home/sever/.superpowers/skills`.

- [ ] **Step 3: Create the curated exports**

Run:

```bash
for skill_name in \
  systematic-debugging \
  test-driven-development \
  verification-before-completion \
  receiving-code-review \
  requesting-code-review \
  writing-plans \
  executing-plans \
  finishing-a-development-branch \
  using-git-worktrees
do
  ln -s \
    "/home/sever/.superpowers/skills/${skill_name}" \
    "/home/sever/.agents/skills/${skill_name}"
done
```

Expected: nine symlinks are created without errors.

### Task 3: Verify the curated discovery set and rollback path

**Files:**
- Verify: `/home/sever/.agents/skills/`
- Verify: `/home/sever/.agents/superpowers-skills-backup`
- Verify: `/home/sever/.superpowers/`

- [ ] **Step 1: Verify the exact exported names**

Run:

```bash
find /home/sever/.agents/skills -mindepth 1 -maxdepth 1 -printf '%f\n' | sort
```

Expected:

```text
executing-plans
finishing-a-development-branch
receiving-code-review
requesting-code-review
systematic-debugging
test-driven-development
using-git-worktrees
verification-before-completion
writing-plans
```

- [ ] **Step 2: Verify every export resolves to a valid skill**

Run:

```bash
for skill_path in /home/sever/.agents/skills/*
do
  test -L "${skill_path}" || exit 1
  test -f "${skill_path}/SKILL.md" || exit 1
done
test "$(readlink /home/sever/.agents/superpowers-skills-backup)" = "/home/sever/.superpowers/skills"
```

Expected: exit 0 with no output.

- [ ] **Step 3: Verify excluded skills are not exported**

Run:

```bash
for skill_name in \
  using-superpowers \
  brainstorming \
  dispatching-parallel-agents \
  subagent-driven-development \
  writing-skills
do
  test ! -e "/home/sever/.agents/skills/${skill_name}" || exit 1
done
```

Expected: exit 0 with no output.

- [ ] **Step 4: Verify the source and user file remain intact**

Run:

```bash
git -C /home/sever/.superpowers status --short --branch
test -f /home/sever/.superpowers/update.sh
```

Expected: no tracked modifications and `?? update.sh` remains.

- [ ] **Step 5: Record the session boundary**

The current Codex session retains its startup skill catalog. Start a new Codex
session and verify that only the nine curated Superpowers skills appear alongside
the system Codex skills.

### Rollback

Use only if link creation or verification fails:

```bash
for skill_name in \
  systematic-debugging \
  test-driven-development \
  verification-before-completion \
  receiving-code-review \
  requesting-code-review \
  writing-plans \
  executing-plans \
  finishing-a-development-branch \
  using-git-worktrees
do
  if test -L "/home/sever/.agents/skills/${skill_name}"
  then
    unlink "/home/sever/.agents/skills/${skill_name}"
  fi
done
mv /home/sever/.agents/superpowers-skills-backup /home/sever/.agents/skills/superpowers
```

Expected: the original umbrella export is restored.
