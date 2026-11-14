# Organizer Verify & Rollback

Use `organizer_verify` after every execution, and `organizer_rollback` when the user wants a change undone.

- `organizer_verify` re-fetches every file in the execution log from Drive and confirms its name and parents match. Success is verified, never assumed — report the per-file result, including any mismatch, plainly.
- If a file did not verify, stop and report it. Do not re-run the execution to "fix" it, and do not guess at the live state — re-fetch it first.
- `organizer_rollback` restores original names and parents from the execution log. It is a dry-run by default: show what would be restored first, then pass `execute: true` only after the user's yes.
- Rollback never deletes anything. A file missing from Drive is reported and skipped, not an error that aborts the rest.
- Offer rollback for every real write, and do not move, rename, or delete anything else until the user answers.
