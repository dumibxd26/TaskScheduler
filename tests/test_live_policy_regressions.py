"""Regression tests for the corrected live policies and preemption planner."""

import pytest

from models.cluster import ClusterScenario, Node
from models.enums import NodeType, PriorityClass, TaskClass, WorkflowClass
from models.profile_store import ProfileStore
from models.workload import (
    TaskInstance,
    TaskTemplate,
    WorkflowInstance,
    WorkflowTemplate,
)
from services.dag_metrics import compute_live_upward_ranks
from services.policy import (
    AdaptivePolicy,
    CalibrationPolicy,
    EFTPolicy,
    HEFTPolicy,
    KubeDefaultPolicy,
    LegacyEightFactorPolicy,
    SchedulerState,
    build_policy,
)
from services.preemption import PreemptionPlanner
from services.queue_manager import QueueManager, TaskEntry
from services.scheduler import PlacementAlgorithm, WorkflowSchedulerRunner


def node(node_id, cpu=2.0, node_type=NodeType.GENERAL):
    return Node(node_id=node_id, node_type=node_type, total_cpu=cpu,
                total_memory=4096, free_cpu=cpu, free_memory=4096)


def build_state(store, nodes, specs, now=1000.0):
    """``specs``: (pod, workflow, template, priority, rank, gang, gang_size, t)."""
    queue = QueueManager()
    templates = {}
    entries = []
    for pod, wf_id, tmpl_id, priority, rank, gang, gang_size, enqueue in specs:
        workflow = queue.admitted_workflows.get(wf_id)
        if workflow is None:
            workflow = WorkflowInstance(
                workflow_instance_id=wf_id, workflow_template_id=wf_id,
                workflow_class=WorkflowClass.BATCH, priority=priority,
                preemptable=True, task_instances={}, arrival_time=0.0)
            queue.admitted_workflows[wf_id] = workflow
            templates[wf_id] = WorkflowTemplate(
                workflow_template_id=wf_id, name=wf_id,
                workflow_class=WorkflowClass.BATCH, default_priority=priority,
                default_preemptable=True, tasks={})
        template = TaskTemplate(
            task_template_id=tmpl_id, name=tmpl_id,
            task_class=TaskClass.CPU_BOUND, cpu_request=1.0,
            memory_request=128, image_name="img",
            compatible_node_types=[NodeType.GENERAL, NodeType.CPU_OPT],
            gang_group_id=gang)
        templates[wf_id].tasks[tmpl_id] = template
        task = TaskInstance(task_instance_id=pod, workflow_instance_id=wf_id,
                            task_template_id=tmpl_id, upward_rank=rank,
                            gang_size=gang_size)
        workflow.task_instances[pod] = task
        entries.append(TaskEntry(
            task=task, workflow=workflow, task_template_id=tmpl_id,
            original_priority=priority, effective_priority=priority,
            enqueue_time=enqueue))
    entries.sort(key=lambda entry: (-entry.effective_priority.value,
                                    entry.enqueue_time))
    return SchedulerState(
        cluster=ClusterScenario("t", "t", "", nodes=nodes),
        queue=queue, profile_store=store, workflow_templates=templates,
        now=now, sorted_tasks=entries)


def warm(store, template, node_id, runtime, startup=1.0, samples=3):
    for _ in range(samples):
        store.record_observation(template, node_id, NodeType.GENERAL,
                                 actual_runtime=runtime, actual_startup=startup)


def test_no_policy_overcommits_for_critical_tasks():
    full = node("full", cpu=1.0)
    full.register_task("other", "running", cpu_request=1.0)
    for name in ("legacy-v1", "adaptive-no-preempt", "eft", "heft",
                 "kube-default"):
        store = ProfileStore()
        runner = WorkflowSchedulerRunner(store, PlacementAlgorithm(store))
        policy = build_policy(name, runner=runner)
        state = build_state(store, [full], [
            ("urgent", "wf", "tmpl", PriorityClass.CRITICAL, 0, None, None, 1)])

        actions = policy.decide(state)

        assert actions.place == [], name
        assert full.free_cpu == 0.0, name


def test_heft_orders_by_controller_rank_and_eft_by_arrival():
    store = ProfileStore()
    for template in ("short", "long"):
        warm(store, template, "only", runtime=5.0)
    specs = [
        ("first", "wf", "short", PriorityClass.BATCH, 3.0, None, None, 1),
        ("critical-path", "wf", "long", PriorityClass.BATCH, 30.0, None, None, 2),
    ]

    heft = HEFTPolicy().decide(build_state(store, [node("only", cpu=1.0)], specs))
    eft = EFTPolicy().decide(build_state(store, [node("only", cpu=1.0)], specs))

    assert [action.task_instance_id for action in heft.place] == ["critical-path"]
    assert [action.task_instance_id for action in eft.place] == ["first"]


def test_eft_prefers_learned_fast_node():
    store = ProfileStore()
    warm(store, "tmpl", "slow", runtime=20.0)
    warm(store, "tmpl", "fast", runtime=5.0)
    state = build_state(store, [node("slow"), node("fast")], [
        ("pod", "wf", "tmpl", PriorityClass.BATCH, 0, None, None, 1)])

    actions = EFTPolicy().decide(state)

    assert actions.place[0].node_id == "fast"
    assert actions.place[0].score == 1000.0 + 1.0 + 5.0


def test_adaptive_places_complete_gang_in_priority_order_and_rolls_back():
    store = ProfileStore()
    nodes = [node("only", cpu=2.0)]
    specs = [
        ("urgent", "wf-high", "solo", PriorityClass.REAL_TIME_HIGH, 0, None, None, 2),
        ("g1", "wf-low", "g1", PriorityClass.BATCH, 0, "gang", 2, 1),
        ("g2", "wf-low", "g2", PriorityClass.BATCH, 0, "gang", 2, 1),
    ]

    actions = AdaptivePolicy().decide(build_state(store, nodes, specs))

    # The urgent task is placed first; the gang no longer fits as a whole and
    # its tentative member reservation is rolled back.
    assert [action.task_instance_id for action in actions.place] == ["urgent"]
    assert {hold.task_instance_id for hold in actions.hold} == {"g1", "g2"}
    assert nodes[0].free_cpu == 1.0


def test_incomplete_gang_is_held_until_every_member_is_queued():
    store = ProfileStore()
    state = build_state(store, [node("only", cpu=4.0)], [
        ("g1", "wf", "g1", PriorityClass.BATCH, 0, "gang", 3, 1),
        ("g2", "wf", "g2", PriorityClass.BATCH, 0, "gang", 3, 1),
    ])

    actions = HEFTPolicy().decide(state)

    assert actions.place == []
    assert {hold.reason for hold in actions.hold} == {"gang_incomplete"}


def test_legacy_places_gangs_atomically():
    store = ProfileStore()
    runner = WorkflowSchedulerRunner(store, PlacementAlgorithm(store))
    nodes = [node("a", cpu=1.0), node("b", cpu=1.0)]
    state = build_state(store, nodes, [
        ("g1", "wf", "g1", PriorityClass.BATCH, 0, "gang", 2, 1),
        ("g2", "wf", "g2", PriorityClass.BATCH, 0, "gang", 2, 1),
    ])

    actions = LegacyEightFactorPolicy(runner).decide(state)

    assert {action.node_id for action in actions.place} == {"a", "b"}
    assert all(item.free_cpu == 0.0 for item in nodes)


def test_kube_default_spreads_by_least_allocated_and_ignores_history():
    store = ProfileStore()
    warm(store, "tmpl", "fast", runtime=1.0)
    warm(store, "tmpl", "slow", runtime=50.0)
    nodes = [node("fast", cpu=2.0), node("slow", cpu=2.0)]
    nodes[0].register_task("other", "busy", cpu_request=1.0)
    state = build_state(store, nodes, [
        ("pod", "wf", "tmpl", PriorityClass.BATCH, 0, None, None, 1)])

    actions = KubeDefaultPolicy(seed=1).decide(state)

    assert actions.place[0].node_id == "slow"


def test_calibration_policy_targets_least_sampled_node():
    store = ProfileStore()
    warm(store, "tmpl", "a", runtime=1.0, samples=2)
    warm(store, "tmpl", "b", runtime=1.0, samples=1)
    state = build_state(store, [node("a"), node("b"), node("c")], [
        ("pod", "wf", "tmpl", PriorityClass.BATCH, 0, None, None, 1)])

    actions = CalibrationPolicy().decide(state)

    assert actions.place[0].node_id == "c"


def test_live_upward_rank_follows_the_critical_path():
    store = ProfileStore()
    warm(store, "io", "n", runtime=4.0, startup=1.0)
    warm(store, "cpu", "n", runtime=10.0, startup=1.0)
    dag = {
        "tasks": {"ingest": "io", "a": "cpu", "b": "cpu", "join": "io"},
        "edges": [["ingest", "a", "DATA", 100.0], ["ingest", "b", "EXECUTION", 0],
                  ["a", "join", "DATA", 0], ["b", "join", "DATA", 0]],
    }

    ranks = compute_live_upward_ranks(dag, store, mean_bandwidth_bytes_s=100.0)

    assert ranks["join"] == 5.0
    assert ranks["a"] == 11.0 + 5.0
    assert ranks["ingest"] == 5.0 + 1.0 + 16.0


class RunningVictim:
    def __init__(self, task_instance_id, elapsed=60.0, remaining=None):
        self.task_instance_id = task_instance_id
        self.cpu_request = 1.0
        self.memory_request = 0.0
        self.elapsed = elapsed
        self.estimated_remaining = remaining


def _preemption_state(planner_nodes, victims_per_node):
    store = ProfileStore()
    specs = [("urgent", "wf-high", "big", PriorityClass.CRITICAL, 0, None, None, 1)]
    state = build_state(store, planner_nodes, specs)
    low = WorkflowInstance(
        workflow_instance_id="wf-low", workflow_template_id="wf-low",
        workflow_class=WorkflowClass.BATCH, priority=PriorityClass.BEST_EFFORT,
        preemptable=True, task_instances={})
    state.queue.admitted_workflows["wf-low"] = low
    template = state.workflow_templates["wf-high"].tasks["big"]
    template.cpu_request = 2.0
    for target, victims in zip(planner_nodes, victims_per_node):
        target.free_cpu = 0.0
        for victim in victims:
            target.active_tasks[victim.task_instance_id] = victim
            low.task_instances[victim.task_instance_id] = TaskInstance(
                task_instance_id=victim.task_instance_id,
                workflow_instance_id="wf-low", task_template_id="low")
    return state


def test_preemption_respects_remaining_node_budget():
    planner = PreemptionPlanner(max_per_node_per_min=1)
    state = _preemption_state(
        [node("a", cpu=2.0)], [[RunningVictim("v0"), RunningVictim("v1")]])

    victims = planner.find_victims(state.sorted_tasks[0], state)

    # Two victims are required but only one preemption is allowed per minute.
    assert victims is None


def test_preemption_does_not_reuse_planned_victims_within_a_tick():
    planner = PreemptionPlanner()
    planner.begin_tick()
    state = _preemption_state(
        [node("a", cpu=2.0)], [[RunningVictim("v0"), RunningVictim("v1")]])
    entry = state.sorted_tasks[0]

    first = planner.find_victims(entry, state)
    second = planner.find_victims(entry, state)

    assert {action.victim_task_instance_id for action in first} == {"v0", "v1"}
    assert second is None


def test_near_completion_threshold_is_configurable():
    planner = PreemptionPlanner(near_completion_s=60.0)

    assert planner._is_near_completion(RunningVictim("v", remaining=30.0))
    assert not PreemptionPlanner()._is_near_completion(
        RunningVictim("v", remaining=30.0))


@pytest.mark.parametrize("child_template_known", [True, False])
def test_live_heft_ra_cap_does_not_count_on_taken_slots(child_template_known):
    # fast has two slots but a blocker holds one. The slot-blind lookahead
    # co-locates the source with all three leaves on fast; the capacity-aware
    # lookahead sees that two leaves would fetch remotely and run slowly. When
    # no leaf Pod has been seen yet, the producer's own size stands in.
    chosen = {}
    for name in ("heft-ra", "heft-ra-cap"):
        store = ProfileStore()
        for node_id, runtime in (("fast", 2.0), ("disk", 0.2), ("spare", 2.0)):
            warm(store, "src", node_id, runtime, startup=0.0)
        for node_id, runtime in (("fast", 3.0), ("disk", 12.0), ("spare", 12.0)):
            warm(store, "leaf", node_id, runtime, startup=0.0)
        fast, disk, spare = node("fast"), node("disk"), node("spare", cpu=1.0)
        fast.register_task("blocker", "blocker-pod", cpu_request=1.0)
        state = build_state(store, [fast, disk, spare], [
            ("source", "wf", "src", PriorityClass.BATCH, 10.0, None, None, 1)])
        if child_template_known:
            state.workflow_templates["wf"].tasks["leaf"] = TaskTemplate(
                task_template_id="leaf", name="leaf",
                task_class=TaskClass.CPU_BOUND, cpu_request=1.0,
                memory_request=128, image_name="img",
                compatible_node_types=[NodeType.GENERAL, NodeType.CPU_OPT])
        source = state.queue.admitted_workflows["wf"].task_instances["source"]
        source.children = [
            (f"leaf-{index}", "leaf", 200 * 1024 * 1024) for index in (1, 2, 3)]

        actions = build_policy(name).decide(state)

        assert len(actions.place) == 1, name
        chosen[name] = actions.place[0].node_id

    assert chosen == {"heft-ra": "fast", "heft-ra-cap": "disk"}
