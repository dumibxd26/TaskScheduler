from models.cluster import ClusterScenario, Node
from models.enums import (
    NodeType,
    PriorityClass,
    TaskClass,
    WorkflowClass,
)
from models.profile_store import ProfileStore
from models.workload import (
    TaskInstance,
    TaskTemplate,
    WorkflowInstance,
    WorkflowTemplate,
)
from services.policy import AdaptivePolicy, SchedulerState
from services.data_placement import DataPlacement
from services.ect import transfer_seconds
from services.policy import LegacyEightFactorPolicy, build_policy
from services.queue_manager import QueueManager, TaskEntry
from services.scheduler import (
    LEGACY_TUNED_WEIGHTS,
    LEGACY_V1_WEIGHTS,
    PlacementAlgorithm,
    WorkflowSchedulerRunner,
)


def policy_state(store, nodes, *, task_instance_id="pod-1", now=1000.0):
    task = TaskInstance(
        task_instance_id=task_instance_id,
        workflow_instance_id="workflow-1",
        task_template_id="template-1",
        upward_rank=1.0,
    )
    template = TaskTemplate(
        task_template_id="template-1",
        name="task",
        task_class=TaskClass.CPU_BOUND,
        cpu_request=1,
        memory_request=128,
        image_name="task:v1",
        compatible_node_types=[NodeType.GENERAL],
    )
    workflow = WorkflowInstance(
        workflow_instance_id="workflow-1",
        workflow_template_id="workflow-template",
        workflow_class=WorkflowClass.BATCH,
        priority=PriorityClass.BATCH,
        preemptable=True,
        task_instances={task_instance_id: task},
    )
    workflow_template = WorkflowTemplate(
        workflow_template_id="workflow-template",
        name="workflow",
        workflow_class=WorkflowClass.BATCH,
        default_priority=PriorityClass.BATCH,
        default_preemptable=True,
        tasks={"template-1": template},
    )
    entry = TaskEntry(
        task=task,
        workflow=workflow,
        task_template_id="template-1",
        original_priority=PriorityClass.BATCH,
        effective_priority=PriorityClass.BATCH,
        enqueue_time=now,
    )
    queue = QueueManager()
    queue.admitted_workflows["workflow-1"] = workflow
    return SchedulerState(
        cluster=ClusterScenario(
            scenario_id="test",
            name="test",
            description="",
            nodes=nodes,
        ),
        queue=queue,
        profile_store=store,
        workflow_templates={"workflow-template": workflow_template},
        now=now,
        sorted_tasks=[entry],
    )


def general_node(node_id):
    return Node(
        node_id=node_id,
        node_type=NodeType.GENERAL,
        total_cpu=4,
        total_memory=4096,
        free_cpu=4,
        free_memory=4096,
    )


def test_live_legacy_variants_configure_the_shared_runner():
    store = ProfileStore()
    runner = WorkflowSchedulerRunner(store, PlacementAlgorithm(store))

    original = build_policy("legacy-v1", runner=runner)
    assert isinstance(original, LegacyEightFactorPolicy)
    assert runner.algorithm.weights == LEGACY_V1_WEIGHTS

    tuned = build_policy("legacy-tuned", runner=runner)
    assert isinstance(tuned, LegacyEightFactorPolicy)
    assert runner.algorithm.weights == LEGACY_TUNED_WEIGHTS


def test_adaptive_forces_coverage_of_unseen_feasible_node():
    store = ProfileStore()
    store.record_observation(
        "template-1",
        "node-a",
        NodeType.GENERAL,
        actual_runtime=5,
        actual_startup=1,
    )
    state = policy_state(store, [general_node("node-a"), general_node("node-b")])

    actions = AdaptivePolicy().decide(state)

    assert actions.place[0].node_id == "node-b"


def test_adaptive_ucb_adjustment_preserves_absolute_ect_scale():
    store = ProfileStore()
    for runtime in (10, 20, 30):
        store.record_observation(
            "template-1",
            "node-a",
            NodeType.GENERAL,
            actual_runtime=runtime,
            actual_startup=1,
        )
    state = policy_state(store, [general_node("node-a")], now=1_000_000.0)

    actions = AdaptivePolicy().decide(state)

    assert actions.place[0].score > state.now


def test_live_dependency_metadata_prices_remote_transfer():
    source = general_node("source")
    destination = general_node("destination")
    cluster = ClusterScenario(
        scenario_id="test",
        name="test",
        description="",
        nodes=[source, destination],
        bandwidth_matrix={("source", "destination"): 100.0},
    )
    task = TaskInstance(
        task_instance_id="child",
        workflow_instance_id="workflow-1",
        task_template_id="child-template",
        parent_node_ids=["source"],
        input_bytes_by_node={"source": 1000},
    )
    template = TaskTemplate(
        task_template_id="child-template",
        name="child",
        task_class=TaskClass.MEMORY_BOUND,
        cpu_request=1,
        memory_request=128,
        image_name="task:v1",
        compatible_node_types=[NodeType.GENERAL],
    )
    workflow = WorkflowInstance(
        workflow_instance_id="workflow-1",
        workflow_template_id="workflow-template",
        workflow_class=WorkflowClass.PIPELINE,
        priority=PriorityClass.BATCH,
        preemptable=True,
        task_instances={"child": task},
    )
    workflow_template = WorkflowTemplate(
        workflow_template_id="workflow-template",
        name="workflow",
        workflow_class=WorkflowClass.PIPELINE,
        default_priority=PriorityClass.BATCH,
        default_preemptable=True,
        tasks={"child-template": template},
    )

    assert transfer_seconds(
        task, template, source, workflow, workflow_template,
        DataPlacement(), cluster.bandwidth_matrix, cluster) == 0
    assert transfer_seconds(
        task, template, destination, workflow, workflow_template,
        DataPlacement(), cluster.bandwidth_matrix, cluster) == 10