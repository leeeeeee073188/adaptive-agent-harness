# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

Repository: `leeeeeee073188/adaptive-agent-harness`

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open`
- Comment: `gh issue comment <number> --body "..."`
- Labels: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`; `gh` does this automatically inside the clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill conventions

- “Publish to the issue tracker” means create a GitHub issue.
- “Fetch the relevant ticket” means run `gh issue view <number> --comments`.
- GitHub issue and PR numbers share one namespace; resolve ambiguous references before acting.

## Wayfinding

- A map is one issue labelled `wayfinder:map`.
- Child tickets use `wayfinder:<type>`.
- Use native GitHub sub-issues and dependencies when available.
- Claim work with `gh issue edit <number> --add-assignee @me`.
- Resolve by commenting with the result and closing the issue.
