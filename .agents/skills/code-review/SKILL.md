---
name: code-review
description: Use when asked to review a Pull Request or code changes.
---

# Review Pull Request

Review a Pull Request (PR) or local changes without modifying the checkout
by default. Inspect the requested diff, run appropriate non-mutating checks
when the correct revision is available, and provide evidence-backed feedback.

## Inputs
* Diff scope (optional):
- If the user specifies a PR ID (e.g. `PR #123`), focus the review on that specific PR.
- If the user mentions a branch name (e.g. `feature/123_add-new-feature`), review the changes in that branch against `main`.
- If no specific scope is provided, review changes made in current branch against `main`.

## Step 0: Check the working tree
1. Inspect the current branch, `git status --short`, and staged/unstaged changes.
2. Preserve unrelated work. Do not switch branches, pull, fetch into local
   refs or create a worktree without authorization.
3. If the requested revision is not checked out, inspect its diff and files
   through existing refs or GitHub read operations. Ask before changing the
   checkout if local execution is needed; do not make switching a prerequisite
   for a read-only review.

## Step 1: Determine scope
1. For a PR, read its base/head commit IDs, description, comments, changed
   files and checks using read-only GitHub operations, preferably `gh`.
2. For a branch, compare its existing ref against the requested base, or
   `main` if none is supplied. Include local changes only when in scope.
3. With no explicit scope, use an open PR's target branch when available,
   otherwise compare the current branch against `main`.
4. Record the reviewed commits and local-change scope. Do not pull merely to
   make the checkout newer. Report unavailable evidence and stale refs.


## Step 2: Read existing comments and PR description
1. Use GitHub PR tools to fetch the PR description and existing comments.
2. Read the description and comments for context and requested review areas.


## Step 3: Read the plan and task
1. If the PR description or comments reference a plan file in `docs/plans/`, read that plan to understand the intent and approach behind the changes.
2. Read linked GitHub issue(s) when present to understand the task requirements and acceptance criteria.

## Step 4: Review the diff
Before classifying findings, read the files at the reviewed revision, from
disk when checked out or through read-only revision/GitHub access otherwise.
Do not rely on memory or mistake the current checkout for another revision.

Classify findings into three priority buckets:
- **Critical**: Issues that must be addressed before the PR can be merged (e.g. failing tests, security vulnerabilities, major bugs, or significant deviations from the plan without justification).
- **Important**: Issues that should be addressed but do not necessarily block merging (e.g. code quality improvements, minor bugs, or deviations from the plan that have some justification).
- **Minor**: Suggestions that are nice to have but not essential (e.g. formatting, minor refactoring, or optional improvements).

Report follow-ups in the review. Do not create issues, comments or files
unless the user requests those writes.

Check following areas:
- Acceptance criteria: Identify unmet requirements and classify by actual impact.
- Plan adherence: Do the changes align with the original plan? If there are deviations, is there a justification provided? Unjustified deviations should be classified as important or critical based on severity.
- Tests: Check coverage and relevant results. Distinguish regressions from
  baseline failures and unavailable hardware/dependencies. Missing tests are
  not automatically critical; explain the specific risk.
- Code quality: Are there any code smells, maintainability issues, or areas for improvement? Classify these as important or minor based on their impact. Use the **smell baseline** below as a checklist on top of anything this repo documents in `.github/instructions/`; a documented repo standard always overrides the baseline, and each smell is a judgement call, not a hard violation. Skip anything already enforced by tooling (linters/analyzers).
  - **Mysterious Name**: a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
  - **Duplicated Code**: the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
  - **Feature Envy**: a method that reaches into another object's data more than its own. → move the method onto the data it envies.
  - **Data Clumps**: the same few fields or params keep travelling together. → bundle them into one type, pass that.
  - **Primitive Obsession**: a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
  - **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
  - **Shotgun Surgery**: one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
  - **Divergent Change**: one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
  - **Speculative Generality**: abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
  - **Message Chains**: long chained navigation (e.g. `a.b().c().d()`) the caller shouldn't depend on. → hide the walk behind one method on the first object.
  - **Middle Man**: a class or function that mostly just delegates onward. → cut it, call the real target direct.
  - **Refused Bequest**: a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.
- Security: Report concrete concerns with evidence and severity proportional
  to likelihood and impact; do not label every concern critical.
- Formatting: Use existing check-only lint/format commands when configured.
  Do not auto-format during review or assume an unconfigured tool blocks CI.
- Documentation: Are there any necessary documentation updates that are missing? Classify these as important or minor based on their impact.
- Simplicity: Is the implementation as simple as it can be while still meeting the requirements? Are there any unnecessary complexities that could be simplified? Classify these as important or minor based on their impact.

Do not flag issues that are already being tracked in the PR comments or linked issues unless they are critical and have not been acknowledged by the author.

## Step 5: Propose fixes
For critical and important issues, propose specific fixes or improvements. If possible, provide code snippets or examples to illustrate the proposed changes. For minor issues, you can simply point them out without needing to provide detailed fixes. Pause for approval.

## Step 6: Implement approved fixes
Approval to implement fixes permits only the agreed edits and validation.
Confirm the intended revision and preserve unrelated work before editing.
Run relevant existing checks afterwards. Do not stage, commit, push or change
branches unless separately authorized by the user.

## Step 7: Capture learnings
Summarize findings, checks performed, limitations and reusable lessons in the
response. Propose documentation updates when useful; do not write unrelated
learning notes as a side effect of a review.

## Rules
* Review the exact requested revision; changing the checkout is not required.
* Never merge the PR yourself. Your role is to review and propose fixes, but the final decision to merge should be made by the author or a designated maintainer after all critical issues are addressed.
