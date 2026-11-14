## What changes and why
<!-- One paragraph. The reviewer reads this before the diff. -->


## User story (required)
<!-- Replace <abbr> and NN. Without this line env-tracking cannot move the story on the board. -->
Refs: code-corhuila/<abbr>-docs#NN


## Promotion trail (only for pull requests into `qa` or `main`)
<!-- List the original commits this pull request re-applies.
     Every commit must carry the line "(cherry picked from commit <sha>)". -->
-


## Checklist
- [ ] Meets the acceptance criteria of the user story
- [ ] Local validation passes (build + tests)
- [ ] Under 400 changed lines, excluding tests and generated files
- [ ] Title follows Conventional Commits
- [ ] Database repositories: only NEW changesets — no applied changeset was edited
- [ ] Into `qa` or `main`: every commit was re-applied with `git cherry-pick -x`
