# Agent instructions

## Commits

Commit automatically, without asking, whenever a code, documentation, or configuration change is complete.
One commit per logical change, with a message that says what changed and why.
Do not wait for the user to ask.

Generated study results, checks, raw run JSON, schedules, manifests, evidence, analysis, and active-study pointers under `results/` stay local.
Never stage, commit, or push these outputs, including result indexes and README files generated inside `results/`.

Before each commit:

- Stage only files that belong to the change. Leave unrelated edits by the user unstaged and mention them.
- Never commit secrets: `.env` and `private/` are ignored; also check the staged diff for API keys (`sk-ant-`, `sk-`) and account numbers.

Commit only; push when the user asks.

## Technical documents

For technical Markdown (`docs/`, `notes/`, result indexes, README):

- One sentence per line; do not hard-wrap mid-sentence. Details: [`.cursor/rules/document-line-breaks.mdc`](.cursor/rules/document-line-breaks.mdc).
- Do not bold arbitrary words; reserve bold for headings or rare definitional labels. Details: [`.cursor/rules/document-emphasis.mdc`](.cursor/rules/document-emphasis.mdc).

Ordinary flowing prose (emails, narrative drafts) is exempt from the line-break rule.
