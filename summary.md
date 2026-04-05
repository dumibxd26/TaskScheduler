# TaskScheduler Summary

At its core, this project is an adaptive task scheduler for DAG-based workflows running on a heterogeneous cluster. The scheduler learns from past executions and improves future placement decisions over time. The learning is kept entirely in memory and is keyed by `task_template_id`, which means the system learns **task placement**, not whole-workflow placement.

## 1. In-Memory Profiling

For each `(task_template_id, node_id)` pair, the system stores a rolling window of the last 20 successful executions.

Each observation records:

- runtime
- startup time
- node CPU usage ratio at placement time
- node memory usage ratio at placement time
- timestamp

The rolling window size is fixed at 20 observations. When a new observation is added and the window exceeds 20, the oldest entries are discarded.

From these observations, the scheduler computes:

- median runtime
- median startup time
- total cost = median runtime + median startup

The median is used instead of the mean because it is more robust to outliers.

The scheduler also aggregates these per-node metrics into per-node-type metrics and maintains:

- `preferred_node_order`: ranked node types for a task
- `preferred_node_ids`: ranked individual nodes for a task
- `failures_by_node`: failure counters used for node-specific penalties

## 2. Workflow Ingestion and Task Readiness

### Submission

When a workflow is submitted:

- the workflow enters the `QUEUED` state
- its tasks start in the `WAITING` state
- it is inserted into the workflow admission heap based on priority and arrival time

### Admission

On each engine tick, workflows are admitted from the heap into the active set:

- the workflow state becomes `ADMITTED`
- it becomes visible to the DAG resolver

### DAG Resolution

The `ReadinessResolver` scans tasks in `WAITING` state:

- if all parents are `FINISHED`, the task becomes ready to run
- if any parent has `FAILED`, the task is marked `FAILED`
- root tasks with no parents are ready immediately

### Ready Queue

Ready tasks are moved from `WAITING` to `READY` and inserted into the ready-task queue, which is ordered by effective priority.

## 3. Feasibility Filtering

Before choosing a node, the engine filters candidate nodes.

A node is feasible if it has:

- a compatible node type
- enough free CPU
- enough free memory

If no feasible nodes exist, the task is held in the `READY` state for a future tick.

## 4. Placement Strategy

When a task is ready and feasible candidate nodes exist, the `WorkflowSchedulerRunner` decides where to place it.

### Phase A: Coverage-First Exploration

Before trusting learned rankings, the scheduler ensures that every feasible compatible node is sampled at least once for the current task template.

- if there are candidate nodes the task has never run on, it chooses randomly among those unseen nodes

This guarantees initial coverage and prevents one lucky early run from dominating future decisions.

### Phase B: Depth Exploration

After every candidate node has been sampled once, the scheduler continues exploring nodes that do not yet have enough data.

- a node is considered under-sampled until it has at least 3 observations for that task
- if such nodes exist, the scheduler chooses randomly among them

This prevents the system from switching to exploitation after just one observation per node.

### Phase C: Normal Operation

Once all candidate nodes have been sampled sufficiently, the scheduler shifts to normal operation:

- with 10% probability, it performs random exploration
- otherwise, it performs score-based exploitation

## 5. Scoring Algorithm

During exploitation, the scheduler scores all feasible candidate nodes and selects the highest-scoring one.

The score combines 8 weighted factors:

1. `type_affinity` (+30)
	Based on historical task performance by node type.

2. `resource_fit` (+20)
	Rewards CPU and memory headroom, plus extra benefit for tasks that can scale with more cores.

3. `availability` (+25)
	Rewards nodes that are expected to free resources soon.

4. `warm_image` (+10)
	Rewards nodes on which this task image has already been executed successfully. This is an execution-level warmness signal, not simply Docker image presence in the cluster cache.

5. `load_balance` (+10)
	Rewards nodes with fewer currently running tasks.

6. `failure_penalty` (-10)
	Penalizes nodes that have failed this task before.

7. `data_locality` (+5)
	Rewards nodes where parent tasks of the current DAG already ran.

8. `memory_pressure` (-15)
	Penalizes nodes that are close to memory exhaustion.

The node with the highest aggregate score wins the placement.

## 6. Reservation and Execution

Once a node is selected:

- the task is registered on that node
- the node records the task in its active-task set
- expected runtime is pulled from historical data if available
- the engine decrements the node's free CPU and free memory immediately to prevent double-booking in the same tick
- the task moves to the `RUNNING` state

Execution can then happen in one of three modes:

- simulation mode
- direct Kubernetes orchestration mode
- server-based orchestration mode

## 7. Feedback and Learning

After execution, the `ExecutionObserver` updates the in-memory profile store.

### On Success

The observer records:

- actual runtime
- actual startup time
- node ID
- node type
- node CPU ratio at placement
- node memory ratio at placement

That observation is added to the rolling 20-observation window for the specific `(task_template_id, node_id)` pair.

Then the scheduler recomputes:

- per-node medians
- per-node-type medians
- preferred node-type order
- preferred node-ID order

The node is also marked warm for that task image, so future runs can receive the warm-image bonus.

### On Failure

The observer records a node-specific failure for that task. This increases the future failure penalty for that node when the same task is scheduled again.

## 8. What the Scheduler Learns

The scheduler learns **task-level placement preferences**.

It does learn:

- which node type is historically best for a specific task
- which individual node is historically best for a specific task
- which nodes tend to fail a specific task
- which nodes are warm for a specific task image

It does not currently learn:

- whole-workflow placement preferences
- workflow-level embeddings or workflow-wide optimization policies
- a single preferred node for an entire workflow

In other words, if the same workflow is submitted many times, the scheduler improves because each repeated task template accumulates more placement history.

## 9. High-Level Learning Loop

The current learning loop is:

1. A workflow is submitted.
2. DAG-ready tasks are moved into the ready queue.
3. The scheduler first ensures node coverage for each task.
4. It then gathers a minimum depth of observations per node.
5. After that, it mostly exploits learned preferences while occasionally exploring.
6. Each finished task updates the rolling metrics.
7. Updated metrics change future node rankings.

Over repeated workflow executions, the scheduler becomes better at placing recurring task templates on the nodes that historically minimize startup time, runtime, and failure risk.