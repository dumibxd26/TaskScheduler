import time
from typing import Callable, Optional

from services.queue_manager import QueueManager
from services.workflow_manager import ReadinessResolver
from services.scheduler import WorkflowSchedulerRunner
from services.policy import (
    Policy, ActionSet, SchedulerState, build_policy,
)
from services.data_placement import DataPlacement
from services.dag_metrics import compute_upward_ranks
from models.enums import TaskState, WorkflowState, PriorityClass, WorkflowClass


class SchedulerEngine:
    def __init__(self, queue_manager: QueueManager, resolver: ReadinessResolver,
                 runner: WorkflowSchedulerRunner, templates: dict,
                 policy: Optional[Policy] = None,
                 data_placement: Optional[DataPlacement] = None,
                 preempt_task: Optional[Callable[[str], bool]] = None):
        self.queue = queue_manager
        self.resolver = resolver
        self.runner = runner
        self.templates = templates  # Map of workflow_template_id -> WorkflowTemplate
        self.data_placement = data_placement or DataPlacement()
        # Policy is the pluggable scheduling brain (Phase 0). When omitted, we
        # build the legacy 8-factor policy that wraps `runner.schedule_task`.
        # Existing callers that pass only the original four arguments keep
        # working unchanged.
        self.policy: Policy = policy or build_policy(runner=runner)
        self.preempt_task = preempt_task

    def run_tick(self, cluster_scenario):
        """
        Dispatch loop: runs every tick (e.g. every 1 second).
        1. Admit queued workflows; compute upward ranks on first admission.
        2. Resolve DAGs to find ready tasks; enforce gang readiness.
        3. Advance vruntime; decay failure counters.
        4. Build SchedulerState; ask the policy for an ActionSet.
        5. Apply the actions (mark RUNNING, drop dispatched entries, log holds).
        """

        # 1. Admit new workflows from the backlog
        while len(self.queue.workflow_queue) > 0:
            wf = self.queue.admit_next_workflow()
            if wf:
                # Compute HEFT upward ranks at admission time.
                tmpl = self.templates.get(wf.workflow_template_id)
                if tmpl:
                    compute_upward_ranks(wf, tmpl, self.runner.profile_store)

        # 2. Resolve DAGs for all active workflows
        for wf_id, workflow in list(self.queue.admitted_workflows.items()):
            template = self.templates[workflow.workflow_template_id]

            ready_tasks = self.resolver.get_ready_tasks(workflow, template)

            # Enforce gang scheduling (H8): hold back gang members unless
            # all members of the group are DAG-ready.
            if ready_tasks:
                ready_tasks = self.resolver.check_gang_readiness(
                    ready_tasks, template)

            if ready_tasks:
                self.queue.enqueue_ready_tasks(ready_tasks, workflow)
                if workflow.state == WorkflowState.ADMITTED:
                    workflow.state = WorkflowState.RUNNING

            self.resolver.check_workflow_terminal(workflow)

            if workflow.state in (WorkflowState.FINISHED, WorkflowState.FAILED):
                # GC data placement entries for finished workflows.
                self.data_placement.gc_workflow(wf_id)
                del self.queue.admitted_workflows[wf_id]

        # 3. Advance vruntime for CFS-style fairness; decay failure counters.
        self.queue.update_vruntime(cluster_scenario)
        self.runner.profile_store.decay_failures()

        # 4. Build read-only state and delegate to the policy.
        sorted_tasks = self.queue.get_sorted_tasks()
        state = SchedulerState(
            cluster=cluster_scenario,
            queue=self.queue,
            profile_store=self.runner.profile_store,
            workflow_templates=self.templates,
            now=time.time(),
            sorted_tasks=sorted_tasks,
            data_placement=self.data_placement,
        )
        actions: ActionSet = self.policy.decide(state)

        # 5. Apply the action set. NOTE: LegacyEightFactorPolicy already calls
        # `runner.schedule_task` internally, which registers the task on the
        # chosen node and decrements virtual capacity. Future policies must
        # either do the same or have the engine register tasks here from the
        # PlaceAction list. We keep both paths robust by using the entry map.
        entry_by_id = {e.task.task_instance_id: e for e in sorted_tasks}
        for preempt in actions.preempt:
            victim = None
            victim_node = None
            for workflow in self.queue.admitted_workflows.values():
                candidate = workflow.task_instances.get(
                    preempt.victim_task_instance_id)
                if candidate is not None:
                    victim = candidate
                    victim_node = candidate.assigned_node_id
                    break
            if victim is None or victim_node is None:
                continue
            if self.preempt_task is not None and not self.preempt_task(
                    victim.task_instance_id):
                continue
            node = next(
                (item for item in cluster_scenario.nodes
                 if item.node_id == victim_node),
                None,
            )
            if node is not None:
                node.unregister_task(victim.task_instance_id)
            victim.state = TaskState.WAITING
            victim.assigned_node_id = None
            victim.start_time = None
            victim.finish_time = None
            victim.preemption_count += 1
            record_preemption = getattr(self.policy, "record_preemption", None)
            if callable(record_preemption):
                record_preemption(victim_node, victim.task_instance_id, state.now)
            print(f"[PREEMPT] '{victim.task_instance_id}' from {victim_node} "
                  f"({preempt.mode})")

        dispatched = set()
        for place in actions.place:
            entry = entry_by_id.get(place.task_instance_id)
            if entry is None:
                continue
            entry.task.state = TaskState.RUNNING
            entry.task.assigned_node_id = place.node_id
            dispatched.add(place.task_instance_id)
            print(f"[DISPATCH] '{place.task_instance_id}' "
                  f"({entry.effective_priority.name}) -> {place.node_id}")

        for hold in actions.hold:
            entry = entry_by_id.get(hold.task_instance_id)
            prio = entry.effective_priority.name if entry else "?"
            print(f"[DISPATCH] HOLD '{hold.task_instance_id}' "
                  f"({prio}) - {hold.reason}")

        # Remove dispatched tasks from the queue
        self.queue.remove_tasks(dispatched)
