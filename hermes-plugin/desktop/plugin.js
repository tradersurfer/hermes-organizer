// Hermes Organizer — live dashboard pane (v0.1.2).
//
// A single ESM file, loaded uncompiled from the unified package
// (hermes-plugin/desktop/plugin.js). It imports only
// '@hermes/plugin-sdk', 'react', and 'react/jsx-runtime' — the three
// specifiers the loader resolves — and writes UI with jsx()/jsxs()
// calls (no JSX syntax, which would not parse uncompiled).
//
// The pane is a VIEW over the proposals / approvals / execution-log
// files the agent tools wrote. It never touches Drive directly: every
// approve / execute / rollback goes through the backend namespace
// (dashboard/plugin_api.py) and the same approval gates the CLI uses
// (per-item approval + proposal-hash match). Execute is a dry-run
// until the user confirms the exact plan shown, in the pane.
//
// Dock: placement 'left' with dock { pane: 'sessions', pos: 'left' }
// lands the pane next to sessions on first open. Placement is a
// semantic role (docs: the pane stacks as tabs with panes of that
// role and the user can drag it anywhere afterward) — a docked
// starting position, not a locked position. Closing the pane's only
// tab disables the plugin; re-enable it in Capabilities → Plugins.
//
// Live updates: a socket to the backend namespace, with a 5 s poll
// as the fallback (the socket is a no-op on OAuth remotes, so the
// poll is the feed there). No hardcoded colors — theme variables
// only (var(--ui-*)); status is shown as text, never color alone.
import { host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState, useEffect, useCallback, useRef } from 'react'

const PLUGIN_ID = 'hermes-organizer'
const POLL_MS = 5000

// ---------------------------------------------------------------- helpers

function post(ctx, path, body) {
  return ctx.rest(path, { method: 'POST', body })
}

function shortName(name) {
  return name && name.length > 42 ? name.slice(0, 39) + '...' : name
}

function confidencePct(value) {
  return typeof value === 'number' ? Math.round(value * 100) + '%' : '—'
}

// ---------------------------------------------------------------- small pieces

function Section(props) {
  return jsxs('div', {
    className: 'flex flex-col gap-1 border-b border-(--ui-border) pb-2',
    children: [
      jsx('div', { className: 'font-medium', children: props.title }),
      props.children,
    ],
  })
}

function Note(props) {
  return jsx('div', {
    className: 'text-(--ui-text-tertiary)',
    children: props.children,
  })
}

function ActionButton(props) {
  return jsx('button', {
    type: 'button',
    disabled: props.disabled,
    onClick: props.onClick,
    className: 'rounded border px-2 py-1 text-left disabled:opacity-50',
    children: props.children,
  })
}

// ---------------------------------------------------------------- pane

function OrganizerPane(props) {
  const ctx = props.ctx
  const [backend, setBackend] = useState('loading') // loading | ok | error
  const [backendError, setBackendError] = useState('')
  const [stateData, setStateData] = useState(null)
  const [proposalsDoc, setProposalsDoc] = useState(null)
  const [approvalsDoc, setApprovalsDoc] = useState(null)
  const [logEntries, setLogEntries] = useState(null)
  const [picked, setPicked] = useState({ proposals: '', approvals: '', log: '' })
  const [checked, setChecked] = useState({})
  const [plan, setPlan] = useState(null)
  const [verifyResult, setVerifyResult] = useState(null)
  const [rollbackPreview, setRollbackPreview] = useState(null)
  const [confirm, setConfirm] = useState(null) // null | 'execute' | 'rollback'
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const mounted = useRef(true)

  const loadState = useCallback(async () => {
    try {
      const data = await ctx.rest('/state')
      if (!mounted.current) return
      if (data && data.status === 'success') {
        setBackend('ok')
        setBackendError('')
        setStateData(data)
        const latest = data.latest || {}
        if (latest.proposals && latest.proposals.doc) {
          setProposalsDoc(latest.proposals.doc)
          setPicked((p) => ({ ...p, proposals: latest.proposals.file }))
        }
        if (latest.approvals && latest.approvals.doc) {
          setApprovalsDoc(latest.approvals.doc)
          setPicked((p) => ({ ...p, approvals: latest.approvals.file }))
        }
        if (latest.log && latest.log.file) {
          setPicked((p) => ({ ...p, log: latest.log.file }))
        }
      } else {
        setBackend('error')
        setBackendError((data && data.error) || 'backend returned an error')
      }
    } catch (err) {
      if (!mounted.current) return
      setBackend('error')
      setBackendError(String((err && err.message) || err))
    }
  }, [ctx])

  useEffect(() => {
    mounted.current = true
    loadState()
    const stopSocket = ctx.socket('/events', () => { loadState() })
    const stopPoll = ctx.setInterval(() => { loadState() }, POLL_MS)
    return () => {
      mounted.current = false
      if (typeof stopSocket === 'function') stopSocket()
      if (typeof stopPoll === 'function') stopPoll()
    }
  }, [ctx, loadState])

  const pickFile = useCallback(async (kind, name) => {
    setPicked((p) => ({ ...p, [kind]: name }))
    if (!name) return
    try {
      if (kind === 'proposals') {
        const data = await ctx.rest('/proposals?file=' + encodeURIComponent(name))
        if (data && data.status === 'success') setProposalsDoc(data.doc)
      } else if (kind === 'approvals') {
        const data = await ctx.rest('/approvals?file=' + encodeURIComponent(name))
        if (data && data.status === 'success') setApprovalsDoc(data.doc)
      } else if (kind === 'log') {
        const data = await ctx.rest('/log?file=' + encodeURIComponent(name))
        if (data && data.status === 'success') setLogEntries(data.entries)
      }
    } catch (err) {
      setNotice('Could not load ' + name + ': ' + String((err && err.message) || err))
    }
  }, [ctx])

  const toggleChecked = useCallback((fileId) => {
    setChecked((c) => ({ ...c, [fileId]: !c[fileId] }))
  }, [])

  const approveSelected = useCallback(async () => {
    const ids = Object.keys(checked).filter((id) => checked[id])
    if (!ids.length || !picked.proposals || !picked.approvals) return
    setBusy(true)
    try {
      const out = await post(ctx, '/approve', {
        proposals_file: picked.proposals,
        approvals_file: picked.approvals,
        approved_file_ids: ids,
        note: 'approved in the Organizer pane',
      })
      if (out.status === 'success') {
        setNotice('Approved ' + out.approved_count + ' item(s).')
        host.notify({ kind: 'info', message: 'Organizer: approved ' + out.approved_count + ' item(s).' })
        setChecked({})
        loadState()
      } else {
        setNotice('Approve refused: ' + (out.error || 'unknown error'))
        host.notify({ kind: 'error', message: 'Organizer approve refused.' })
      }
    } catch (err) {
      setNotice('Approve failed: ' + String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }, [ctx, checked, picked, loadState])

  const dryRun = useCallback(async () => {
    if (!picked.proposals || !picked.approvals) return
    setBusy(true)
    setConfirm(null)
    try {
      const out = await post(ctx, '/plan', {
        proposals_file: picked.proposals,
        approvals_file: picked.approvals,
      })
      if (out.status === 'success') {
        setPlan(out)
      } else {
        setNotice('Dry-run refused: ' + (out.error || 'unknown error'))
      }
    } catch (err) {
      setNotice('Dry-run failed: ' + String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }, [ctx, picked])

  const executeConfirmed = useCallback(async () => {
    if (!picked.proposals || !picked.approvals) return
    setBusy(true)
    try {
      const out = await post(ctx, '/execute', {
        proposals_file: picked.proposals,
        approvals_file: picked.approvals,
        log_file: picked.log || 'execution-log.jsonl',
        execute: true,
      })
      if (out.status === 'success' && out.mode === 'executed') {
        const bad = (out.verification || []).filter((v) => !v.ok).length
        setNotice('Executed ' + out.executed.length + ' file(s). ' + (bad ? bad + ' did NOT verify — see below.' : 'All verified ok.'))
        host.notify({ kind: bad ? 'error' : 'info', message: 'Organizer: executed ' + out.executed.length + ' file(s).' })
        setVerifyResult({ log_path: out.log_path, verification: out.verification })
        setPlan(null)
        setConfirm(null)
        loadState()
      } else {
        setNotice('Execute refused: ' + (out.error || (out.refused && out.refused.join('; ')) || 'unknown error'))
        host.notify({ kind: 'error', message: 'Organizer execute refused.' })
      }
    } catch (err) {
      setNotice('Execute failed: ' + String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }, [ctx, picked, loadState])

  const verifyLog = useCallback(async () => {
    if (!picked.log) return
    setBusy(true)
    try {
      const out = await ctx.rest('/verify?log=' + encodeURIComponent(picked.log))
      if (out.status === 'success') {
        setVerifyResult({ log_path: picked.log, verification: out.verification })
        setNotice('Verified ' + out.verified_ok + '/' + out.total + ' ok.')
      } else {
        setNotice('Verify failed: ' + (out.error || 'unknown error'))
      }
    } catch (err) {
      setNotice('Verify failed: ' + String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }, [ctx, picked])

  const rollbackDryRun = useCallback(async () => {
    if (!picked.log) return
    setBusy(true)
    setConfirm(null)
    try {
      const out = await post(ctx, '/rollback', { log_file: picked.log })
      if (out.status === 'success') {
        setRollbackPreview(out)
      } else {
        setNotice('Rollback dry-run refused: ' + (out.error || 'unknown error'))
      }
    } catch (err) {
      setNotice('Rollback dry-run failed: ' + String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }, [ctx, picked])

  const rollbackConfirmed = useCallback(async () => {
    if (!picked.log) return
    setBusy(true)
    try {
      const out = await post(ctx, '/rollback', { log_file: picked.log, execute: true })
      if (out.status === 'success' && out.mode === 'executed') {
        setNotice('Restored ' + out.restored.length + ' file(s).' + (out.missing.length ? ' ' + out.missing.length + ' missing.' : ''))
        host.notify({ kind: 'info', message: 'Organizer: restored ' + out.restored.length + ' file(s).' })
        setRollbackPreview(null)
        setConfirm(null)
        loadState()
      } else {
        setNotice('Rollback refused: ' + (out.error || 'unknown error'))
      }
    } catch (err) {
      setNotice('Rollback failed: ' + String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }, [ctx, picked, loadState])

  // ------------------------------------------------------------ render

  const files = (stateData && stateData.files) || { proposals: [], approvals: [], logs: [] }
  const entries = (proposalsDoc && proposalsDoc.proposals) || []
  const actionable = entries.filter((e) => e.proposed_filename)
  const review = entries.filter((e) => !e.proposed_filename)
  const checkedCount = Object.keys(checked).filter((id) => checked[id]).length
  const approvedMap = {}
  if (approvalsDoc && approvalsDoc.proposals) {
    approvalsDoc.proposals.forEach((e) => { approvedMap[e.file_id] = !!e.approved })
  }
  const planSteps = (plan && plan.steps) || []
  const planRefused = (plan && plan.refused) || []
  const verifyRows = (verifyResult && verifyResult.verification) || []
  const wouldRestore = (rollbackPreview && rollbackPreview.would_restore) || []

  const proposalRows = actionable.map((e) => {
    const isChecked = !!checked[e.file_id]
    const isApproved = !!approvedMap[e.file_id]
    return jsxs('label', {
      key: e.file_id,
      className: 'flex flex-col gap-0.5 border-b pb-1',
      children: [
        jsxs('span', {
          children: [
            jsx('input', {
              type: 'checkbox',
              checked: isChecked,
              disabled: isApproved,
              onChange: () => toggleChecked(e.file_id),
            }),
            jsx('span', { children: ' ' + shortName(e.original_name) }),
          ],
        }),
        jsx('span', {
          className: 'text-(--ui-text-tertiary)',
          children: '→ ' + shortName(e.proposed_filename) + ' · ' + (e.subcategory || '') + ' · ' + confidencePct(e.confidence) + (isApproved ? ' · approved' : ''),
        }),
      ],
    })
  })

  const reviewRows = review.map((e) => jsx('div', {
    key: e.file_id,
    className: 'text-(--ui-text-tertiary)',
    children: shortName(e.original_name) + ' — human review (no proposal)',
  }))

  const planRows = planSteps.map((s) => jsx('div', {
    key: s.file_id,
    children: shortName(s.original_name) + ' → ' + shortName(s.proposed_filename),
  }))
  const refusedRows = planRefused.map((r, i) => jsx('div', {
    key: 'refused-' + i,
    children: 'Refused: ' + r,
  }))
  const verifyRowsEl = verifyRows.map((v) => jsx('div', {
    key: v.file_id,
    children: '[' + (v.ok ? 'ok' : 'MISMATCH') + '] ' + shortName(v.expected_name) + (v.ok ? '' : ' — Drive shows ' + shortName(v.actual_name)),
  }))
  const restoreRows = wouldRestore.map((r) => jsx('div', {
    key: r.file_id,
    children: 'would restore: ' + shortName(r.restore_name),
  }))

  const fileOptions = (names) => names.map((n) => jsx('option', { key: n, value: n, children: n }))

  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 overflow-y-auto p-3 text-sm',
    children: [
      jsxs('div', {
        children: [
          jsx('div', { className: 'font-medium', children: 'Hermes Organizer' }),
          jsx(Note, { children: 'Live view of the proposals, approvals, and execution logs the agent tools wrote. Nothing here touches Drive directly.' }),
        ],
      }),
      backend === 'loading' ? jsx(Note, { children: 'Connecting to the Organizer backend…' }) : null,
      backend === 'error' ? jsxs(Section, {
        title: 'Backend unavailable',
        children: [
          jsx('div', { children: backendError || 'The backend did not answer.' }),
          jsx(Note, { children: 'Enable the Organizer plugin in Capabilities → Plugins, add hermes-organizer to plugins.enabled in config.yaml, and restart the gateway. The pane degrades to this notice until then.' }),
        ],
      }) : null,
      notice ? jsx('div', { children: notice }) : null,
      backend === 'ok' ? jsxs(Section, {
        title: 'Files',
        children: [
          jsxs('label', {
            children: [
              jsx('span', { children: 'Proposals: ' }),
              jsx('select', {
                value: picked.proposals,
                onChange: (ev) => pickFile('proposals', ev.target.value),
                children: fileOptions(files.proposals),
              }),
            ],
          }),
          jsxs('label', {
            children: [
              jsx('span', { children: 'Approvals: ' }),
              jsx('select', {
                value: picked.approvals,
                onChange: (ev) => pickFile('approvals', ev.target.value),
                children: fileOptions(files.approvals),
              }),
            ],
          }),
          jsxs('label', {
            children: [
              jsx('span', { children: 'Execution log: ' }),
              jsx('select', {
                value: picked.log,
                onChange: (ev) => pickFile('log', ev.target.value),
                children: fileOptions(files.logs),
              }),
            ],
          }),
          jsx(Note, { children: entries.length + ' proposed · ' + actionable.length + ' actionable · ' + review.length + ' in review · ' + Object.keys(approvedMap).filter((id) => approvedMap[id]).length + ' approved' }),
        ],
      }) : null,
      backend === 'ok' && entries.length ? jsxs(Section, {
        title: 'Proposals — check the ones to approve',
        children: [
          ...proposalRows,
          ...reviewRows,
          jsx(ActionButton, {
            disabled: busy || !checkedCount || !picked.proposals || !picked.approvals,
            onClick: approveSelected,
            children: checkedCount ? 'Approve ' + checkedCount + ' selected' : 'Approve selected',
          }),
          jsx(Note, { children: 'Approving records your yes for these exact items. A re-scan changes the proposals and the old approval is refused as stale.' }),
        ],
      }) : null,
      backend === 'ok' && !entries.length ? jsx(Note, { children: 'No proposals file yet — run a scan from the agent first.' }) : null,
      backend === 'ok' ? jsxs(Section, {
        title: 'Execute — dry-run first, always',
        children: [
          jsx(ActionButton, {
            disabled: busy || !picked.proposals || !picked.approvals,
            onClick: dryRun,
            children: 'Dry-run the approved plan',
          }),
          ...refusedRows,
          ...planRows,
          plan && !planRefused.length && planSteps.length ? jsx(Note, { children: planSteps.length + ' step(s) ready. Check them, then execute.' }) : null,
          plan && !planRefused.length && planSteps.length ? (
            confirm === 'execute'
              ? jsxs('div', {
                children: [
                  jsx(ActionButton, { disabled: busy, onClick: executeConfirmed, children: 'Confirm: execute these ' + planSteps.length + ' step(s)' }),
                  jsx(ActionButton, { disabled: busy, onClick: () => setConfirm(null), children: 'Cancel' }),
                ],
              })
              : jsx(ActionButton, { disabled: busy, onClick: () => setConfirm('execute'), children: 'Execute the dry-run plan' })
          ) : null,
          jsx(Note, { children: 'Execute renames and moves only approved items. Nothing is ever deleted.' }),
        ],
      }) : null,
      backend === 'ok' ? jsxs(Section, {
        title: 'Verify',
        children: [
          jsx(ActionButton, {
            disabled: busy || !picked.log,
            onClick: verifyLog,
            children: 'Verify the execution log against live Drive',
          }),
          ...verifyRowsEl,
        ],
      }) : null,
      backend === 'ok' ? jsxs(Section, {
        title: 'Rollback',
        children: [
          jsx(ActionButton, {
            disabled: busy || !picked.log,
            onClick: rollbackDryRun,
            children: 'Preview what rollback would restore',
          }),
          ...restoreRows,
          rollbackPreview && wouldRestore.length ? (
            confirm === 'rollback'
              ? jsxs('div', {
                children: [
                  jsx(ActionButton, { disabled: busy, onClick: rollbackConfirmed, children: 'Confirm: restore ' + wouldRestore.length + ' file(s)' }),
                  jsx(ActionButton, { disabled: busy, onClick: () => setConfirm(null), children: 'Cancel' }),
                ],
              })
              : jsx(ActionButton, { disabled: busy, onClick: () => setConfirm('rollback'), children: 'Roll back from this log' })
          ) : null,
          jsx(Note, { children: 'Rollback restores original names and folders. It never deletes anything.' }),
        ],
      }) : null,
      jsx(Note, { children: 'Data dir: ' + ((stateData && stateData.data_dir) || '—') + ' · refreshes every 5 s' }),
    ],
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Hermes Organizer',
  // The desktop half ships opt-in: it inventories in Capabilities →
  // Plugins but stays disabled until the user flips it on.
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Organizer',
      data: {
        placement: 'left',
        dock: { pane: 'sessions', pos: 'left' },
        width: '340px',
      },
      render: () => jsx(OrganizerPane, { ctx }),
    })
  },
}
