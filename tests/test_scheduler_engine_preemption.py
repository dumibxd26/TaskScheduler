from engine import SchedulerEngine
from models.cluster import ClusterScenario, Node
from models.enums import (
    NodeType,
    PriorityClass,
    TaskClass,
    TaskState,
    WorkflowClass,
    WorkflowState,
)
from models.profile_store import ProfileStore
from models.workload import (
    TaskInstance,
    TaskTemplate,
    WorkflowInstance,
    WorkflowTemplate,
)
from services.policy import ActionSet, PreemptAction
from services.queue_manager import QueueManager
from services.scheduler import PlacementAlgorithm, WorkflowSchedulerRunner
from services.workflow_manager import ReadinessResolver


class PreemptingPolicy:
    def __init__(self):
        self.recorded = []

    def decide(self, state):
        return ActionSet(preempt=[PreemptAction("victim")])

    def record_preemption(self, node_id, task_id, now):
        self.recorded.append((node_id, task_id, now))


def test_engine_applies_accepted_preemption_and_releases_capacity():
    store = ProfileStore()
    runner = WorkflowSchedulerRunner(store, PlacementAlgorithm(store))
    queue = QueueManager()
    victim = TaskInstance(
        task_instance_id="victim",
        workflow_instance_id="workflow-1",
        task_template_id="victim-template",
        state=TaskState.RUNNING,
        assigned_node_id="node-a",
    )
    workflow = WorkflowInstance(
        workflow_instance_id="workflow-1",
        workflow_template_id="workflow-template",
        workflow_class=WorkflowClass.BATCH,
        priority=PriorityClass.BATCH,
        preemptable=True,
        task_instances={"victim": victim},
        state=WorkflowState.RUNNING,
    )
    queue.admitted_workflows["workflow-1"] = workflow
    task_template = TaskTemplate(
        task_template_id="victim-template",
        name="victim",
        task_class=TaskClass.CPU_BOUND,
        cpu_request=2,
        memory_request=512,
        image_name="task:v1",
        compatible_node_types=[NodeType.GENERAL],
    )
    workflow_template = WorkflowTemplate(
        workflow_template_id="workflow-template",
        name="workflow",
        workflow_class=WorkflowClass.BATCH,
        default_priority=PriorityClass.BATCH,
        default_preemptable=True,
        tasks={"victim-template": task_template},
    )
    node = Node(
        node_id="node-a",
        node_type=NodeType.GENERAL,
        total_cpu=4,
        total_memory=4096,
        free_cpu=4,
        free_memory=4096,
    )
    node.register_task(
        "victim-template", "victim", cpu_request=2, memory_request=512)
    cluster = ClusterScenario(
        scenario_id="test",
        name="test",
        description="",
        nodes=[node],
    )
    policy = PreemptingPolicy()
    callbacks = []
    engine = SchedulerEngine(
        queue,
        ReadinessResolver(),
        runner,
        {"workflow-template": workflow_template},
        policy=policy,
        preempt_task=lambda task_id: callbacks.append(task_id) or True,
    )

    engine.run_tick(cluster)

    assert callbacks == ["victim"]
    assert victim.state == TaskState.WAITING
    assert victim.assigned_node_id is None
    assert victim.preemption_count == 1
    assert node.active_tasks == {}
    assert node.free_cpu == 4
    assert node.free_memory == 4096
    assert policy.recorded[0][:2] == ("node-a", "victim")