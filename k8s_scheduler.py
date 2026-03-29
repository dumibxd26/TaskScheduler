import time
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

# Import the logic we wrote earlier
# from services.scheduler import ProfileStore, PlacementAlgorithm, WorkflowSchedulerRunner
# from models.workload import TaskInstance, TaskTemplate
# from models.enums import TaskClass

SCHEDULER_NAME = "thesis-scheduler"
NAMESPACE = "default"

class K8sScheduler:
    def __init__(self, runner):
        # Load kube config (uses ~/.kube/config locally, or service account in-cluster)
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
            
        self.v1 = client.CoreV1Api()
        self.runner = runner

    def get_cluster_state(self):
        """Fetches current nodes from K8s and translates them to your Node models."""
        # For now, we mock the cluster state based on K8s nodes.
        # In reality, you'd read node labels like `node-type: MEM_OPT` here.
        k8s_nodes = self.v1.list_node().items
        # Return a mock ClusterScenario or a list of your internal Node objects
        pass 

    def bind_pod(self, pod_name, node_name):
        """Sends the Binding object to K8s to actually place the Pod."""
        target = client.V1ObjectReference(api_version='v1', kind='Node', name=node_name)
        meta = client.V1ObjectMeta(name=pod_name)
        binding = client.V1Binding(target=target, metadata=meta)
        
        try:
            self.v1.create_namespaced_pod_binding(name=pod_name, namespace=NAMESPACE, body=binding)
            print(f"[BINDING] Successfully bound Pod {pod_name} to Node {node_name}")
        except ApiException as e:
            print(f"Exception when binding pod: {e}")

    def run(self):
        print(f"Starting {SCHEDULER_NAME} watch loop...")
        w = watch.Watch()
        
        # Watch all pods in the namespace
        for event in w.stream(self.v1.list_namespaced_pod, namespace=NAMESPACE):
            pod = event['object']
            
            # We only care about Pending pods assigned to OUR scheduler that aren't bound yet
            if pod.status.phase == 'Pending' and \
               pod.spec.scheduler_name == SCHEDULER_NAME and \
               pod.spec.node_name is None:
                
                print(f"[SCHEDULER] Found pending pod: {pod.metadata.name}")
                
                # 1. Extract metadata from Pod Annotations (passed by your Workflow Manager)
                annotations = pod.metadata.annotations or {}
                wf_template_id = annotations.get('thesis.scheduler/workflow_template_id', 'unknown_wf')
                task_template_id = annotations.get('thesis.scheduler/task_template_id', 'unknown_task')
                
                # Translate K8s Pod to your internal model
                # task_instance = TaskInstance(...)
                # task_template = TaskTemplate(...)
                # cluster_scenario = self.get_cluster_state()
                
                try:
                    # 2. Run your algorithm (Fast path / Slow path)
                    # selected_node = self.runner.schedule_task(task_instance, task_template, cluster_scenario)
                    
                    # Mocking the node selection for this skeleton
                    selected_node_name = "kind-worker2" # Replace with selected_node.node_id
                    
                    # 3. Bind the pod in K8s
                    self.bind_pod(pod.metadata.name, selected_node_name)
                    
                except Exception as e:
                    print(f"[ERROR] Failed to schedule pod {pod.metadata.name}: {e}")

if __name__ == '__main__':
    # Initialize your in-memory stores and runner
    # store = ProfileStore()
    # algo = PlacementAlgorithm()
    # runner = WorkflowSchedulerRunner(store, algo)
    
    # Start the K8s loop
    # scheduler = K8sScheduler(runner)
    # scheduler.run()
    pass