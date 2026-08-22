# Issue tracker: GitHub

Issues and specifications for this repository live in GitHub Issues. Use the `gh` CLI for all operations.

## Conventions

- Create issues with `gh api repos/{owner}/{repo}/issues -X POST` or `gh issue create`.
- Pass Markdown bodies through a single-quoted argument, a quoted heredoc, or JSON input. Shell-interpolated bodies can execute backticks and corrupt the issue text.
- Read an issue with `gh issue view <number> --comments` and fetch its labels.
- List issues with `gh issue list --state open --json number,title,body,labels,comments` plus the needed `--label` and `--state` filters.
- Comment with `gh issue comment <number> --body "..."`.
- Apply or remove labels with `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- Close an issue with `gh issue close <number> --comment "..."`.

Infer the repository from `git remote -v`; `gh` does this automatically inside this checkout.

## Pull requests as a triage surface

PRs as a request surface: no.

GitHub shares one number space across issues and pull requests. For an ambiguous `#42`, run `gh pr view 42` and fall back to `gh issue view 42`.

## Skill operations

- When a skill says "publish to the issue tracker", create a GitHub issue.
- When a skill says "fetch the relevant ticket", run `gh issue view <number> --comments`.

## Wayfinding operations

The map is one issue with child issues as tickets.

- Label the map `wayfinder:map`.
- Link child tickets as GitHub sub-issues. If sub-issues are unavailable, add each child to a task list in the map and put `Part of #<map>` at the top of the child body.
- Label children `wayfinder:<type>`, where type is `research`, `prototype`, `grilling`, or `task`.
- Use GitHub issue dependencies for blockers. If dependencies are unavailable, put `Blocked by: #<n>` at the top of the child body.
- Claim work with `gh issue edit <number> --add-assignee @me`.
- Resolve work by commenting with the result, closing the issue, and recording the decision link in the map.
