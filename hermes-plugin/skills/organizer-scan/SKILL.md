# Organizer Scan

Use `organizer_scan` to index and classify a folder, then `organizer_propose` to write the proposals file.

- Scan a local folder with `folder`, or a Drive folder with `drive_folder_id` (read-only list + working copies; originals never touched).
- Vision is a second opinion only: `vision: "auto"` (default) runs it on review / thin-text files, `"off"` is OCR-only, `"always"` runs it on every image. With no provider credentials the scan runs OCR-only and says so — that is normal, not an error.
- The scan proposes only. Nothing is renamed, moved, or deleted by a scan.
- Files below 0.60 confidence go to human review with no proposed filename. Do not guess a name for them; show them to the user.
- Write the proposals file with `organizer_propose` (or `proposals_path` on the scan). Every entry starts `approved: false` with a `proposal_hash` binding the file ID to its proposed name and folder.
- Never lower the threshold, never weaken keywords to chase a bad scan, and never present a proposal as a result — a proposal is a suggestion until the user approves it.
