# Organizer Approve & Execute

Use `organizer_approve` then `organizer_execute`. The gates are the point — never work around them.

- Show the user the exact plan first: every file's old name -> proposed name, and the destination folder. Quote the approval back with its exact items, scope, and destination.
- Call `organizer_approve` ONLY after the user approved those exact items in this conversation. It records the approval; it never invents one. Never edit, copy, or backfill an approvals file to satisfy a gate.
- A re-scan changes the proposals, the hashes change, and the old approval is refused as stale. Approval binds to ONE scan's proposals — never re-scan between approval and execution.
- `organizer_execute` is a dry-run by default. Read the dry-run plan and check it matches the approved plan exactly (same files, same names, same folder, zero refusals) before passing `execute: true`.
- Pass `execute: true` only after the user's explicit yes on that exact plan. A casual ack or reaction is never a yes.
- If any item is missing, changed, renamed, or outside the approved folder, stop that item and report it — do not execute the rest on a guess.
- Nothing is ever deleted. Every executed change is written to the execution log and verified live afterwards.
