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
from models.enums import TaskState, WorkflowState

# Results a bind callback may return for one placement.
BIND_OK = "bound"
BIND_RETRY = "retry"      # transient failure: keep the task queued
BIND_DROP = "drop"        # the Pod no longer exists: forget the queue entry


class SchedulerEngine:
    """One scheduling tick: admission, readiness, policy decision, actions.

    The same engine drives two surfaces:

    * the in-process engine (tests, demos), which owns DAG readiness and
      re-queues preempted tasks itself, and
    * the Kubernetes adapter, where the controller owns readiness. There the
      adapter disables ``resolve_dependencies``, passes ``preempt_task`` to
      delete victim Pods (the controller recreates them), and passes
      ``bind_task`` so failed bindings are rolled back instead of being
      reported as dispatched.
    """

    def __init__(self, queue_manager: QueueManager, resolver: ReadinessResolver,
                 runner: WorkflowSchedulerRunner, templates: dict,
                 policy: Optional[Policy] = None,
                 data_placement: Optional[DataPlacement] = None,
                 preempt_task: Optional[Callable[[str], bool]] = None,
                 bind_task: Optional[Callable[[str, str], str]] = None,
                 resolve_dependencies: bool = True,
                 clock: Callable[[], float] = time.time):
        self.queue = queue_manager
        self.resolver = resolver
        self.runner = runner
        self.templates = templates  # workflow_template_id -> WorkflowTemplate
        self.data_placement = data_placement or DataPlacement()
        self.policy: Policy = policy or build_policy(runner=runner)
        self.preempt_task = preempt_task
        self.bind_task = bind_task
        self.resolve_dependencies = resolve_dependencies
        self.clock = clock

    def run_tick(self, cluster_scenario):
        """
        1. Admit queued workflows; compute upward ranks on first admission.
        2. Resolve DAGs to find ready tasks; enforce gang readiness.
        3. Advance vruntime; decay failure counters.
        4. Build SchedulerState; ask the policy for an ActionSet.
        5. Apply preemptions, bind placements (rolling back failures), log holds.
        """

        # 1. Admit new workflows from the backlog
        while len(self.queue.workflow_queue) > 0:
            wf = self.queue.admit_next_workflow()
            if wf:
                tmpl = self.templates.get(wf.workflow_template_id)
                if tmpl:
                    compute_upward_ranks(wf, tmpl, self.runner.profile_store)

        # 2. Resolve DAGs for all active workflows (in-process mode only)
        if self.resolve_dependencies:
            for wf_id, workflow in list(self.queue.admitted_workflows.items()):
                template = self.templates[workflow.workflow_template_id]

                ready_tasks = self.resolver.get_ready_tasks(workflow, template)
                if ready_tasks:
                    ready_tasks = self.resolver.check_gang_readiness(
                        ready_tasks, template)

                if ready_tasks:
                    self.queue.enqueue_ready_tasks(ready_tasks, workflow)
                    if workflow.state == WorkflowState.ADMITTED:
                        workflow.state = WorkflowState.RUNNING

                self.resolver.check_workflow_terminal(workflow)

                if workflow.state in (WorkflowState.FINISHED, WorkflowState.FAILED):
                    self.data_placement.gc_workflow(wf_id)
                    del self.queue.admitted_workflows[wf_id]

        # 3. Advance vruntime for CFS-style fairness; decay failure counters.
        self.queue.update_vruntime(cluster_scenario)
        self.runner.profile_store.decay_failures()

        # 4. Build state and delegate to the policy.
        sorted_tasks = self.queue.get_sorted_tasks()
        state = SchedulerState(
            cluster=cluster_scenario,
            queue=self.queue,
            profile_store=self.runner.profile_store,
            workflow_templates=self.templates,
            now=self.clock(),
            sorted_tasks=sorted_tasks,
            data_placement=self.data_placement,
        )
        actions: ActionSet = self.policy.decide(state)

        # 5a. Preemptions.
        entry_by_id = {e.task.task_instance_id: e for e in sorted_tasks}
        nodes_by_id = {node.node_id: node for node in cluster_scenario.nodes}
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
            node = nodes_by_id.get(victim_node)
            if node is not None:
                node.unregister_task(victim.task_instance_id)
            # In Kubernetes the controller recreates the deleted Pod and the
            # adapter re-queues it when it appears; the in-process engine
            # re-queues it through readiness resolution instead.
            victim.state = (
                TaskState.PREEMPTED if self.preempt_task is not None
                else TaskState.WAITING)
            victim.assigned_node_id = None
            victim.start_time = None
            victim.finish_time = None
            victim.preemption_count += 1
            record_preemption = getattr(self.policy, "record_preemption", None)
            if callable(record_preemption):
                record_preemption(victim_node, victim.task_instance_id, state.now)
            print(f"[PREEMPT] '{victim.task_instance_id}' from {victim_node} "
                  f"({preempt.mode}) at={state.now:.3f}")

        # 5b. Placements and bindings.
        dispatched = set()
        dropped = set()
        for place in actions.place:
            entry = entry_by_id.get(place.task_instance_id)
            if entry is None:
                continue
            result = BIND_OK
            if self.bind_task is not None:
                result = self.bind_task(place.task_instance_id, place.node_id)
            if result != BIND_OK:
                node = nodes_by_id.get(place.node_id)
                if node is not None:
                    node.unregister_task(place.task_instance_id)
                if result == BIND_DROP:
                    dropped.add(place.task_instance_id)
                print(f"[DISPATCH] bind {result} for '{place.task_instance_id}' "
                      f"-> {place.node_id}; reservation rolled back")
                continue
            entry.task.state = TaskState.RUNNING
            entry.task.assigned_node_id = place.node_id
            entry.task.start_time = state.now
            dispatched.add(place.task_instance_id)
            print(f"[DISPATCH] '{place.task_instance_id}' "
                  f"({entry.effective_priority.name}) -> {place.node_id}")

        for hold in actions.hold:
            entry = entry_by_id.get(hold.task_instance_id)
            prio = entry.effective_priority.name if entry else "?"
            print(f"[DISPATCH] HOLD '{hold.task_instance_id}' "
                  f"({prio}) - {hold.reason}")

        self.queue.remove_tasks(dispatched | dropped)
        return actions
