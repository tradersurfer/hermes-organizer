"""Tool schemas — what the LLM sees. Hermes Organizer plugin v0.1.0.

Each schema's description states the tool's gate in plain language,
so the model cannot mistake a dry-run for an execution or an approval
for a guess. No business data is shipped in this plugin; folders,
file IDs, and approvals all come from the user at runtime.
"""

ORGANIZER_SCAN = {
    "name": "organizer_scan",
    "description": (
        "Scan a local folder (or a Google Drive folder) and propose "
        "renames: OCR/vision-classify each image and propose a new "
        "filename and taxonomy folder. READ-ONLY — nothing is renamed, "
        "moved, or deleted. Use 'folder' for a local path or "
        "'drive_folder_id' for Drive. Vision is a second opinion only; "
        "with no credentials the scan runs OCR-only. Optionally writes "
        "a scan report JSON ('report_path') and a proposals file "
        "('proposals_path')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "Local folder path to scan."},
            "drive_folder_id": {"type": "string", "description": "Drive folder ID to scan (read-only list + download working copies)."},
            "download_dir": {"type": "string", "description": "Where Drive working copies go (Drive scans only). Defaults to a temp dir."},
            "limit": {"type": "integer", "description": "Max Drive files (default 10)."},
            "vision": {"type": "string", "description": "'off' (OCR only), 'auto' (review/thin-text files only, default), or 'always'."},
            "vision_provider": {"type": "string", "description": "'cloudflare' (default) or 'gemini'."},
            "report_path": {"type": "string", "description": "Optional path to write the scan JSON report."},
            "proposals_path": {"type": "string", "description": "Optional path to write the proposals file."},
            "audit_log": {"type": "string", "description": "Optional JSONL audit log path."},
        },
    },
}

ORGANIZER_PROPOSE = {
    "name": "organizer_propose",
    "description": (
        "Turn a scan report into a proposals file: one entry per file "
        "with a proposal_hash binding the file ID to its proposed "
        "filename and folder, all approved: false. Nothing is approved "
        "here — that is organizer_approve's job, after the user's yes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scan_report": {"type": "string", "description": "Scan JSON path from organizer_scan."},
            "proposals_path": {"type": "string", "description": "Path to write the proposals file."},
        },
        "required": ["scan_report", "proposals_path"],
    },
}

ORGANIZER_APPROVE = {
    "name": "organizer_approve",
    "description": (
        "Record the user's approval for exact proposal entries. Call "
        "this ONLY after the user approved the exact items in this "
        "conversation, with their exact proposed names and destination "
        "folder. It records that approval — it never invents one. "
        "Entries not listed stay approved: false. Human-review entries "
        "and unknown file IDs are refused and no approvals file is "
        "written."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "proposals_path": {"type": "string", "description": "Proposals file path."},
            "approvals_path": {"type": "string", "description": "Path to write the approvals file."},
            "approved_file_ids": {"type": "array", "items": {"type": "string"}, "description": "File IDs the user approved, exactly as listed in the proposals file."},
            "approve_all": {"type": "boolean", "description": "Approve every actionable entry. Only when the user approved all of them."},
            "note": {"type": "string", "description": "Short note recorded with the approval (e.g. what the user said)."},
        },
        "required": ["proposals_path", "approvals_path"],
    },
}

ORGANIZER_EXECUTE = {
    "name": "organizer_execute",
    "description": (
        "Dry-run the approved plan by default: shows exactly what "
        "would be renamed and moved, with zero Drive writes. Pass "
        "execute: true ONLY after the user approved the exact plan — "
        "the run refuses unless every actionable proposal is approved "
        "with a matching proposal_hash and its target name is free in "
        "the target folder. Nothing is ever deleted; every change is "
        "written to the execution log and then verified live."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "proposals_path": {"type": "string", "description": "Proposals file path."},
            "approvals_path": {"type": "string", "description": "Approvals file path."},
            "log_path": {"type": "string", "description": "Execution log path (default execution-log.jsonl)."},
            "execute": {"type": "boolean", "description": "False (default) = dry-run only. True = perform the renames + moves for real, after the user's approval."},
            "root_folder_id": {"type": "string", "description": "Drive folder ID the target paths resolve from (default 'root')."},
        },
        "required": ["proposals_path", "approvals_path"],
    },
}

ORGANIZER_VERIFY = {
    "name": "organizer_verify",
    "description": (
        "Re-fetch every file in an execution log from Drive and "
        "confirm its name and parents match what was executed. "
        "Read-only. Success is verified, never assumed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "log_path": {"type": "string", "description": "Execution log path (JSONL)."},
        },
        "required": ["log_path"],
    },
}

ORGANIZER_ROLLBACK = {
    "name": "organizer_rollback",
    "description": (
        "Restore original names and parents from an execution log. "
        "Dry-run by default (shows what would be restored, zero "
        "writes). Pass execute: true to perform the restores. Never "
        "deletes anything; a file missing from Drive is reported and "
        "skipped."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "log_path": {"type": "string", "description": "Execution log path (JSONL)."},
            "execute": {"type": "boolean", "description": "False (default) = dry-run only. True = perform the restores."},
        },
        "required": ["log_path"],
    },
}

ORGANIZER_REPORT = {
    "name": "organizer_report",
    "description": (
        "Render one or more scan JSON reports as a single "
        "self-contained HTML page with thumbnails, categories, "
        "confidences, and proposed renames."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scan_report": {"type": "string", "description": "Scan JSON path, or list of paths."},
            "image_dir": {"type": "string", "description": "Folder holding the scanned images, for thumbnails."},
            "out_path": {"type": "string", "description": "HTML report path to write."},
        },
        "required": ["scan_report", "out_path"],
    },
}
