from dataclasses import dataclass, field
from typing import List, Set
from models.enums import NodeType

@dataclass
class Node:
    node_id: str
    node_type: NodeType
    total_cpu: float
    total_memory: float
    free_cpu: float
    free_memory: float
    running_tasks: int = 0
    # Stores image names that are currently warm/cached on this exact machine
    warm_images: Set[str] = field(default_factory=set) 

@dataclass
class ClusterScenario:
    scenario_id: str
    name: str
    description: str
    nodes: List[Node] = field(default_factory=list)