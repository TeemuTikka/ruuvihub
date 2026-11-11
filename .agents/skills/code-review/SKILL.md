---
name: code-review
description: Use when asked to review a Pull Request or code changes.
---

# Review Pull Request

This skill guides you through reviewing a Pull Request (PR) in this repository: check out the PR branch locally, review code changes, run relevant checks, and provide clear feedback.

## Inputs
* Diff scope (optional):
- If the user specifies a PR ID (e.g. `PR #123`), focus the review on that specific PR.
- If the user mentions a branch name (e.g. `feature/123_add-new-feature`), review the changes in that branch against `main`.
- If no specific scope is provided, review changes made in current branch against `main`.

## Step 0: Branch check
1. Run `git branch --show-current` to get the current branch name.
2. If the branch name is `main`, or on any other branch that does not match the expected PR branch:
    - **Pause:** Present the expected branch name based on the PR ID or diff scope (e.g. `feature/123_add-new-feature`).
    - Offer the user to switch to the correct branch (e.g. `git checkout <name>`).
    - If the user declines, stop the process and inform them that switching to the correct branch is required to review the PR.

## Step 1: Determine scope
1. If a PR ID was provided, fetch the PR branch from GitHub:
   ```bash
   git fetch origin pull/<PR_ID>/head:pr-<PR_ID>
   git checkout pr-<PR_ID>
   ```
2. If a branch name was provided, check it out directly:
   ```bash
   git checkout <branch_name>
   ```
3. If no specific scope was provided, check if there's a PR open for the current branch. If there is, use current branch diff against target branch (e.g. `main`) as the scope for review.
4. Run `git pull` to ensure you have the latest changes from the remote.
5. If the PR cannot be checked out (e.g. due to permissions or if it doesn't exist), report the issue clearly and stop.
6. If a PR was checked out successfully, read PR details (description, comments, changed files, checks) using GitHub PR tools to understand the context.


## Step 2: Read existing comments and PR description
1. Use GitHub PR tools to fetch the PR description and existing comments.
2. Read through the description and comments to understand the context of the changes, any specific areas


## Step 3: Read the plan and task
1. If the PR description or comments reference a plan file in `docs/plans/`, read that plan to understand the intent and approach behind the changes.
2. Read linked GitHub issue(s) when present to understand the task requirements and acceptance criteria.

## Step 4: Review the diff
Before classifying any findings, always re-read the files from disk. Don't rely on memory or conversation history for file contents.

Classify findings into three priority buckets:
- **Critical**: Issues that must be addressed before the PR can be merged (e.g. failing tests, security vulnerabilities, major bugs, or significant deviations from the plan without justification).
- **Important**: Issues that should be addressed but do not necessarily block merging (e.g. code quality improvements, minor bugs, or deviations from the plan that have some justification).
- **Minor**: Suggestions that are nice to have but not essential (e.g. formatting, minor refactoring, or optional improvements).

Log follow-ups if needed so that the issues can be addressed in a future.

Check following areas:
- Acceptance criteria: Are all acceptance criteria from the issue/task met? If not, classify as critical.
- Plan adherence: Do the changes align with the original plan? If there are deviations, is there a justification provided? Unjustified deviations should be classified as important or critical based on severity.
- Tests: Are there tests covering the new changes? Do all tests pass? Missing tests or failing tests should be classified as critical.
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
- Security: Are there any security vulnerabilities or concerns in the code? Classify these as critical.
- Formatting: run linters and formatters to check for any formatting issues. These block the build and should be fixed before merging, but can be classified as minor if they are purely stylistic and do not affect functionality.
- Documentation: Are there any necessary documentation updates that are missing? Classify these as important or minor based on their impact.
- Simplicity: Is the implementation as simple as it can be while still meeting the requirements? Are there any unnecessary complexities that could be simplified? Classify these as important or minor based on their impact.

Do not flag issues that are already being tracked in the PR comments or linked issues unless they are critical and have not been acknowledged by the author.

## Step 5: Propose fixes
For critical and important issues, propose specific fixes or improvements. If possible, provide code snippets or examples to illustrate the proposed changes. For minor issues, you can simply point them out without needing to provide detailed fixes. Pause for approval.

## Step 6: Implement approved fixes
If the user approves the proposed fixes, implement them directly in the PR branch. After making changes, run tests and linters locally to ensure everything is passing. Commit the changes with a clear message referencing the PR and the issue being addressed (e.g. "Fix critical security vulnerability in XYZ component").

## Step 7: Capture learnings
After the review is complete, summarize the key findings and any follow-up actions that need to be taken. If there were critical issues that had to be fixed, consider whether there are any learnings that can be applied to future PRs or development practices to prevent similar issues from occurring again. Document these learnings in a shared location, like docs/ or README.md, so that the whole team can benefit from them.

## Rules
* Always check out the PR branch to review the changes in context. Do not rely on memory or conversation history for file contents.
* Never merge the PR yourself. Your role is to review and propose fixes, but the final decision to merge should be made by the author or a designated maintainer after all critical issues are addressed.
