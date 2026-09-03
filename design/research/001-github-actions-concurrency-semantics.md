# Research 001 — GitHub Actions workflow-level `concurrency` semantics (2026)

Date: 2026-09-01 · Freshness matters: **yes** — GitHub Actions features and billing change frequently; recheck annually.

## Question

In a GitHub Actions workflow triggered by `push: main`, `schedule: cron`, and `workflow_dispatch`, with a slow integration suite (~45 min) plus a separate `chaos` job, how should concurrency be configured to ensure only the newest merge commit consumes CI time while earlier pending runs are cancelled? Specifically:

1. Exact current syntax and placement of workflow-level vs job-level `concurrency`
2. Concurrency group semantics: how many runs may be pending; what happens to middle runs when three arrive in succession
3. Collision risk: do `push: main`, scheduled cron, and `workflow_dispatch` events share or isolate concurrency groups based on `github.ref` alone?
4. Recommended group expressions to keep scheduled/dispatch runs out of the push group
5. Context variable availability across event types and fallback idioms
6. Cancellation signal semantics and grace periods
7. Docker container cleanup guarantees when a run is cancelled mid-testcontainers execution
8. Impact on required-status-checks and branch protection
9. Billable minutes: does cancellation actually save cost?

## Findings

### 1. Syntax: Workflow-Level and Job-Level `concurrency`

- **Placement**: `concurrency` appears as a top-level workflow key (affects the entire workflow run) or at the job level (controls a single job). Both accept the same `group` and `cancel-in-progress` fields — [Control the concurrency of workflows and jobs](https://docs.github.com/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs) (official GitHub Docs)
- **Mandatory fields**: `group` (string, case-insensitive) identifies the concurrency group; `cancel-in-progress` (boolean, default `false`) controls whether to cancel in-progress runs when a new one enqueues — [Control the concurrency of workflows and jobs](https://docs.github.com/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs) (official GitHub Docs)
- **Newer option (`queue`)**: As of May 2026, `queue: max` allows up to 100 pending runs in FIFO order within a concurrency group, replacing the old one-pending-run limit. `queue: single` (default) restores the legacy "one pending max" behavior. **Constraint**: `queue: max` and `cancel-in-progress: true` cannot both be set — validation error — [GitHub Actions concurrency groups now allow larger queues](https://github.blog/changelog/2026-05-07-github-actions-concurrency-groups-now-allow-larger-queues/) (GitHub Changelog, May 2026)
- **Syntax examples**:
  ```yaml
  # Workflow-level, cancel old runs on new push
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: true

  # Workflow-level, queue up to 100 runs
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    queue: max
  ```

### 2. Concurrency Group Semantics: Pending Runs and Middle-Run Behavior

- **Default limit** (without `queue: max`): At most one job or workflow run may be pending per concurrency group at any time; only one may execute. A new trigger while one is running enqueues as pending — [Control the concurrency of workflows and jobs](https://docs.github.com/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs) (official GitHub Docs)
- **Three consecutive pushes, `cancel-in-progress: true`**: 
  - Run A starts and executes
  - Commit B (arrives while A is in-flight) becomes pending
  - Commit C (arrives while A is still running and B is pending) **cancels B, then B is replaced by C as the new pending run**
  - Once A finishes, C starts; B's run is permanently lost
  - **Quoted from community documentation**: "A new trigger immediately cancels any running instance. At most, only one job or workflow run can be pending in a concurrency group at a time" — [Three GitHub Actions concurrency patterns that prevent duplicate cron runs](https://dev.to/morinaga/three-github-actions-concurrency-patterns-that-prevent-duplicate-cron-runs-27l5) (DEV Community, citing official behavior)
- **Three consecutive pushes, `cancel-in-progress: false` or with `queue: max`**: All three runs queue in FIFO order (up to 100 total); the middle run is preserved and will eventually execute — [GitHub Actions concurrency groups now allow larger queues](https://github.blog/changelog/2026-05-07-github-actions-concurrency-groups-now-allow-larger-queues/) (GitHub Changelog, May 2026)

### 3. Risk: `github.ref` Collision Across Event Types

- **`github.ref` behavior by event**:
  - `push: main`: `github.ref` = `"refs/heads/main"` (the pushed branch)
  - `schedule: cron`: `github.ref` = `"refs/heads/<default-branch>"` (typically `refs/heads/main`)
  - `workflow_dispatch`: `github.ref` = `"refs/heads/<default-branch>"` or the branch selected in the UI
  - **Result**: A concurrency group keyed only on `github.ref` (e.g., `${{ github.workflow }}-${{ github.ref }}`) will **collide** — a scheduled cron run and a push-to-main run share the same group and will cancel each other if `cancel-in-progress: true` — [Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts) (official GitHub Docs)

### 4. Recommended Group Expression: Isolating Event Types

- **Canonical pattern** (separate groups by event type): Use `github.event_name` to keep triggers isolated:
  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}
    cancel-in-progress: false  # or true, depending on desire
  ```
  This ensures:
  - Push-to-main runs use group `"<workflow>-push-refs/heads/main"`
  - Scheduled cron runs use group `"<workflow>-schedule-refs/heads/main"`
  - Manual dispatch runs use group `"<workflow>-workflow_dispatch-refs/heads/main"`
  - These three groups never collide and don't cancel each other — [Three GitHub Actions concurrency patterns that prevent duplicate cron runs](https://dev.to/morinaga/three-github-actions-concurrency-patterns-that-prevent-duplicate-cron-runs-27l5) (DEV Community)

- **Alternative (for push-only cancellation, preserve schedule/dispatch)**: If you want only *push* events to cancel in-progress runs, use conditional `cancel-in-progress`:
  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: ${{ github.event_name == 'push' }}
  ```
  This is not an official documented pattern but follows the same principle of using `github.event_name` to gate behavior.

### 5. Context Variable Availability and Fallback Idioms

| Variable | Available on | Value | Unavailable (empty) on |
|---|---|---|---|
| `github.event_name` | All events (push, schedule, pull_request, workflow_dispatch, …) | String: `"push"`, `"schedule"`, `"workflow_dispatch"`, etc. | Never |
| `github.ref` | push, schedule, pull_request, workflow_dispatch | Full ref: `"refs/heads/main"`, `"refs/tags/v1.0"` | Never (always has a value) |
| `github.head_ref` | pull_request, pull_request_target only | Source branch of PR (e.g., `"feature-branch"`) | **push, schedule, workflow_dispatch** |
| `github.run_id` | All events | Unique workflow run ID (integer) | Never |

- **Recommended fallback idiom for branch-based concurrency** (handles PR, push, schedule, dispatch):
  ```yaml
  group: ${{ github.workflow }}-${{ github.head_ref || github.ref_name }}
  ```
  - On PR: uses the source branch (`github.head_ref`, e.g., `"feature-branch"`)
  - On push/schedule/dispatch: uses the target branch (`github.ref_name`, e.g., `"main"`)
  - **Source**: [Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts) (official GitHub Docs)

- **Fallback for truly unique groups per event** (when no branch filtering is desired):
  ```yaml
  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.run_id }}
  ```
  - Every event gets its own unique concurrency group; no cancellation across different run types.

### 6. Cancellation Signal Semantics and Grace Periods

- **Signal sequence**:
  1. Server evaluates all running job `if` conditions (e.g., `if: always()` jobs continue; others are marked for cancellation)
  2. For each step marked for cancellation, the runner sends `SIGINT` (Ctrl-C) to the entry process
  3. Runner waits 7.5 seconds for graceful exit
  4. If still running, sends `SIGTERM` (Ctrl-Break)
  5. Runner waits another 2.5 seconds
  6. If still running, forcibly terminates the process tree
  7. After a **5-minute total cancellation timeout**, the server forcibly terminates all remaining jobs and steps — [Workflow cancellation reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation) (official GitHub Docs)

- **Child process caveat**: Bash (the default shell) does not propagate SIGINT/SIGTERM to child processes while it is blocked waiting for them to exit. As a result, **Docker containers started by your test suite (testcontainers) may not receive the cancellation signal** and may remain running on the runner — [Workflow cancellation reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation) (official GitHub Docs); confirmed by [Root Docker container exiting immediately on SIGTERM](https://github.com/actions/actions-runner-controller/issues/2151) (GitHub Actions Runner Controller issue)

- **Cleanup guarantee for `if: always()` steps**: Steps with `if: always()` are guaranteed to run even during cancellation, provided the job itself hasn't been forcibly terminated. This allows a final cleanup step to attempt to remove Docker containers — [Workflow cancellation reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation) (official GitHub Docs)

### 7. Docker Container Cleanup When Cancelled

- **Problem**: When a workflow is cancelled while testcontainers are running, the bash shell does not propagate the signal to child Docker processes, so containers may remain running on the ephemeral runner — [Many times container stuck when action cancelled](https://github.com/game-ci/unity-actions/issues/125) (GitHub issue)

- **Documented solution (via `runs.post`)**: GitHub Actions metadata allows an action to define a `runs.post` step that executes **after** the action, "regardless of failure, crash, timeout, or cancellation" — [Workflow cancellation reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation) (official GitHub Docs)

- **Practical cleanup**: If you control the test action, add a `runs.post` cleanup that runs `docker kill $(docker ps -q)` and `docker system prune --force --volumes` — [Cleanup Docker Containers created by Github Action](https://github.com/marketplace/actions/cleanup-docker-containers-created-by-github-action) (GitHub Marketplace action, community solution)

- **For GitHub-hosted runners**: GitHub automatically cleans up ephemeral runners after each job, so lingering containers are destroyed with the runner instance. No manual cleanup is required for cost purposes, but self-hosted runners require explicit cleanup — confirmed by [Automatic Docker Cleanup](https://github.com/actions/runner/issues/926) (GitHub Actions Runner issue)

- **Testcontainers-specific note**: Testcontainers includes an internal cleanup daemon (Ryuk) that attempts to remove containers even on cancellation. However, relying on this alone is not safe; a final `if: always()` cleanup step invoking `docker system prune` is recommended for self-hosted runners — [Automatic Docker Cleanup](https://github.com/actions/runner/issues/926) (GitHub Actions Runner issue)

### 8. Cancellation and Required Status Checks / Branch Protection

- **No special status-check handling for cancelled runs**: When a workflow run is cancelled, its final status is `cancelled`. If that run is a *required* status check, the PR's merge state may become **stuck** in "waiting for status to be reported" because a cancelled run does not resolve as either success or failure — [Status checks "required if run"](https://github.com/orgs/community/discussions/26092) (GitHub Community Discussion)

- **Recommended practice**: Make cancelable workflows (like the `integration.yml` in your case, which is explicitly not a required check) **optional** rather than required in branch protection. A required check should be deterministic: must finish (not be cancelled) or fail/succeed clearly — confirmed by [How to Handle Cancelable Github Actions which Require Status Checks to Pass Before Merging](https://medium.com/picus-security-engineering/how-to-handle-cancelable-github-actions-which-require-status-checks-to-pass-before-merging-63545083da4e) (Picus Security Engineering, Medium)

- **For your case**: Since `integration.yml` is explicitly **not** a required check, cancellation poses no branch-protection risk.

### 9. Billable Minutes: Does Cancellation Save Cost?

- **Official documentation is silent on billable-minute behavior upon cancellation.** GitHub's billing docs state you are charged for "actual execution time used" and cite examples of partial minutes (e.g., a job that fails after 5 minutes uses 5 minutes) — [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-github-actions) (official GitHub Docs, as of Sept 2026)

- **Empirical evidence from industry** (not official GitHub policy): Using concurrency with `cancel-in-progress: true` to kill stale runs saves 20–30% of total billable minutes on high-frequency teams, suggesting that a cancelled run's billable time is capped at the execution time *before cancellation*, not the full scheduled timeout — [GitHub Actions Cost Optimization: Cut Your Billable Minutes by 40–70%](https://tenki.cloud/blog/github-actions-cost-optimization) (Tenki Blog, 2026)

- **2026 pricing context**: On 1 January 2026, GitHub reduced GitHub-hosted runner rates by up to 39% (folding a $0.002/min platform charge into lower per-minute rates). Cancellation benefit is clearest on high-churn workflows where you save the *difference* between a full 45-minute run and an early cancellation at, say, minute 2 — [Reduced pricing for GitHub-hosted runners usage](https://github.blog/changelog/2026-01-01-reduced-pricing-for-github-hosted-runners-usage/) (GitHub Changelog)

- **Conclusion**: Cancellation almost certainly saves billable minutes (stop the meter when the run is no longer needed), but GitHub does not publicly document the exact semantics. For your 45-minute integration suite, using `cancel-in-progress: true` on the push trigger should save significant cost by preventing multiple sequential pushes from each taking 45 minutes.

## Options Compared: Concurrency Strategies for Your Scenario

| Strategy | Group Expression | `cancel-in-progress` | Behaviour | Best for |
|---|---|---|---|---|
| **A: No isolation, full cancellation** | `${{ github.workflow }}-${{ github.ref }}` | `true` | Push to main cancels cron/dispatch; cron cancels push. All runs in same group. | Single-event workflows (push-only) |
| **B: Event-type isolation** | `${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}` | `false` or `true` | Push, cron, and dispatch each get separate groups. Internal cancellation per event type. | **Recommended for your case**: lets manual dispatch debug run proceed without cancelling cron or vice versa. |
| **C: Full serialization** | `${{ github.workflow }}-${{ github.ref }}` | `false` | All runs queue sequentially (or up to 100 with `queue: max`). No cancellation. | Deployments requiring strict ordering; all commits must eventually run. |
| **D: Push cancels, others queue** | `${{ github.workflow }}-${{ github.ref }}`; conditional: `cancel-in-progress: ${{ github.event_name == 'push' }}` | conditional | Push events cancel in-progress; schedule/dispatch queue without cancelling each other. | Moderate: push is fresh code, cron is routine maintenance. |

**Recommendation**: For your use case (slow 45-min integration suite with optional chaos job, not a required check):
- Use **Strategy B** (event-type isolation) at the workflow level: `concurrency: { group: "${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}", cancel-in-progress: true }`
- This allows rapid pushes to main to cancel each other, but lets nightly cron and manual debug runs proceed without colliding.
- Chaos job inherits the same concurrency group (no job-level override needed) since it's part of the same workflow.

## Version/Compatibility Notes

- **`cancel-in-progress` field**: Available since GitHub Actions general availability (2019); syntax and semantics stable through September 2026.
- **`queue` field**: Introduced 7 May 2026 (GitHub Changelog); allows `queue: max` for up to 100 pending runs. Requires `cancel-in-progress: false` or omitted (default). Older workflows without `queue:` behave as `queue: single` (one pending max).
- **`github.event_name` context**: Available in all events since 2019; stable.
- **Cancellation timeout (5 minutes)**: Documented in workflow-cancellation reference; no override knobs.
- **Runner lifecycle**: Ephemeral GitHub-hosted runners destroyed after each job (automatic cleanup). Self-hosted runners persist and require manual Docker cleanup.

## Evidence Gaps

1. **Exact billable-minute accounting upon cancellation**: GitHub documentation does not specify whether a cancelled run incurs charges only for execution time before cancellation, or has other billing rules. Industry practice and changelog posts suggest minutes saved, but the exact calculation is not public.
2. **Testcontainers + cancellation on GitHub-hosted runners**: No official statement on whether Testcontainers' internal cleanup daemon (Ryuk) successfully cleans up containers if the runner is forcibly terminated after 5 minutes. Empirically, containers are destroyed when the ephemeral runner is retired, but the exact cleanup sequence is not documented.
3. **Deprecated `on: merge_group` event and concurrency interaction**: A newer event type (`merge_group`, used by GitHub's merge queue) is not covered here; unclear whether it has its own concurrency semantics distinct from push/schedule/dispatch.
4. **Conditional `cancel-in-progress` evaluation timing**: Whether `cancel-in-progress: ${{ github.event_name == 'push' }}` is evaluated at workflow-queue time (before the run is queued) or at execution time is not officially documented. Recommendation: treat it as evaluated at queue time and test empirically in your repo.

## Librarian's Note

**What the sources indicate**: GitHub Actions `concurrency` groups with `cancel-in-progress: true` are the correct mechanism for your use case. The key risk — collision between `push: main` and `schedule: cron` — is real: both set `github.ref` to the same value (the default branch), and a group keyed only on `github.ref` will cause them to cancel each other. The canonical fix is to include `github.event_name` in the group expression, isolating the three event types into separate concurrency groups. This is documented in community patterns but not explicitly recommended in the official GitHub Actions reference (which focuses on individual knobs rather than multi-event workflows). Cancellation signals do not reliably reach Docker containers started by testcontainers (bash doesn't propagate signals to child processes), so a final `if: always()` cleanup step or `runs.post` is advisable for self-hosted runners; ephemeral GitHub-hosted runners auto-cleanup and need no special handling. No evidence that cancellation of non-required checks affects branch protection. Billable minutes are almost certainly capped at actual execution time before cancellation (not full timeout), but GitHub does not publish this guarantee.

