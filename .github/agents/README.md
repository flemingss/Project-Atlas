# Agent fleet refactor

Five specialist agents plus the orchestrator, organized by **what kind of check
confirms the output** rather than by task topic.

| Agent | Tier | Job | Verified by |
|---|---|---|---|
| Scout | T1 flash | Read-only investigation | Claims carry `file:line` |
| Inspector | T1 flash | Runs checks, enumerates diffs, scans patterns | Exit codes |
| Patcher | T1 flash | Applies fully specified changes | Acceptance command red→green |
| Analyst | T2 mid | Review, design, requirements, failure adjudication | Nothing cheap — needs a real model |
| Implementer | T2 mid | Scoped changes needing judgment | Its own diff plus checks |
| Orchestrator | T3 | Decides, routes, integrates | You |

## What changed and why

**Receipt protocol.** Every subagent writes full output to
`.agent/<task-id>/<role>-<n>.md` and returns a capped envelope: status, report
path, one-line summary bullets, and a call count. The orchestrator pulls the
report only when the summary is insufficient. This is the single biggest cost
lever — previously every subagent's full prose landed in the orchestrator's
context and was reprocessed on every subsequent turn.

**Context discipline.** The orchestrator gets a hard budget of 5 direct file
reads per session and is barred from reading diffs to review them. Ownership
now means deciding, not doing. The old prompt said "review every diff," which
guaranteed context growth proportional to code volume.

**Failure ladder.** Nothing previously specified what happens when an agent
bonks, so the default was takeover. Now: classify, re-delegate to the *same*
tier with the gap closed, escalate only on second failure, take over only the
blocking step, and hand the remainder back down. Escalations are counted; three
means re-plan, five means tell the user.

**Task sizing.** S/M/L gate up front. The old prompt made heavy intake
mandatory for nearly everything, which is a large fixed cost on small tasks.

**The ledger.** `.agent/<task-id>/ledger.md` holds objective, acceptance
criteria, decisions, ownership map, work items, and escalation count. The
orchestrator rebuilds state from it instead of from conversation history, and
compacts at every phase boundary. This is what makes multi-session work
possible without unbounded context.

**Review split.** Mechanical review (linters, type checks, tests, pattern
scans) goes to Inspector on flash; judgment goes to Analyst on mid. Review on a
flash model was unverifiable, so it got redone at the top tier — paying twice.

**Fleet consolidation.** `RequirementsAnalyst`, `ArchitectureReviewer`,
`SecurityReviewer`, `TestStrategist`, and `CodeReviewer` collapse into one
focus-parameterized `Analyst`. Seven specialist reports landing simultaneously
cost the orchestrator seven reconciliations for heavily overlapping content.

**Prompt specificity inverted.** Flash agents now get explicit procedures, tool
budgets, and stop conditions. They previously had the *shortest* prompts in the
set, which is backwards — a weaker model given a vague objective wanders,
burns calls, and returns something the orchestrator has to redo.

## Wiring checklist

1. **Check for dangling agent references first.** Your old orchestrator listed
   eight agents against five files. If those four names did not resolve, the
   orchestrator was failing to spawn them and absorbing the work — which alone
   would explain a lot of what you are seeing.
2. Drop the six files in your agents directory; delete the old five.
3. Confirm `.agent/` is writable and gitignored.
4. Confirm every agent can write to its report path — Scout, Inspector, and
   Analyst are read-only on *source*, but must be able to write reports. If
   your framework's `read`/`search` toolset blocks all writes, either grant a
   scratch-write tool or have the orchestrator pass a path the framework's
   report mechanism handles.

## Ledger template

```markdown
# Objective
<one paragraph, concrete desired outcome>

# Acceptance criteria
- [ ] <checkable statement>

# Decisions
- <decision> — <one-line rationale>

# Ownership map
| Surface | Owner | State |
|---|---|---|

# Work items
| id | state | tier | depends-on | report |
|---|---|---|---|---|

# Escalations
count: 0

# Open questions
- blocking: <...>
- deferred: <...>
```

## Tuning knobs

Start with the defaults, then adjust based on the cost line in the completion
report:

- **Orchestrator read budget** (5). If it is routinely exhausted, tasks are
  being cut too coarsely.
- **Scout tool budget** (20) and read cap (5 files). Raise for large
  monorepos, lower if scouts return diffuse results.
- **Analyst read budget** (10). If regularly hit, Inspector is not supplying
  enough up front.
- **Patcher routing gate** (four conditions). Loosen if Patcher is idle,
  tighten if it returns `blocked` often.
- **Escalation thresholds** (3 to re-plan, 5 to stop).

## Model placement

Placement is now a routing decision the orchestrator makes per assignment, so
moving models is cheap. Two things worth trying once it is stable:

- **Analyst on the top tier for security and architecture focus only.** Those
  are the reviews where a miss is most expensive and least detectable.
- **Inspector always on the cheapest available model.** It runs commands and
  reports output; there is nothing there a larger model does better.

The one placement to avoid reverting: review judgment on flash. It reads as a
saving and is not.
