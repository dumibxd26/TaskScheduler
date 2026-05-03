import time
import json
from typing import Optional, List, Dict
from models.enums import NodeType
from models.profile import TaskProfile, NodeMetrics, Observation, NodeTypeMetrics

# Bumped when the JSON schema changes; load_json accepts older versions.
PROFILE_STORE_SCHEMA_VERSION = 2


class ProfileStore:
    def __init__(self):
        self.profiles: dict[str, TaskProfile] = {}

    def get_profile(self, task_template_id: str) -> Optional[TaskProfile]:
        return self.profiles.get(task_template_id)

    def get_preferred_order(self, task_template_id: str) -> List[NodeType]:
        profile = self.get_profile(task_template_id)
        if profile and profile.preferred_node_order:
            return profile.preferred_node_order
        return []

    def get_preferred_nodes(self, task_template_id: str) -> List[str]:
        """Returns ranked list of individual node IDs (best first)."""
        profile = self.get_profile(task_template_id)
        if profile and profile.preferred_node_ids:
            return profile.preferred_node_ids
        return []

    def get_completion_level(self, task_template_id: str) -> float:
        """Returns how well-explored this task's scheduling options are (0.0–1.0)."""
        profile = self.get_profile(task_template_id)
        if profile:
            return profile.exploration_level
        return 0.0

    def get_node_median_runtime(self, task_template_id: str, node_id: str) -> Optional[float]:
        """Returns the median runtime for a task on a specific node, or None if unknown."""
        profile = self.get_profile(task_template_id)
        if profile and node_id in profile.metrics_by_node:
            m = profile.metrics_by_node[node_id]
            if m.count > 0:
                return m.median_runtime
        return None

    def get_expected_runtime(self, task_template_id: str, node_id: str) -> Optional[float]:
        """Returns median_runtime + median_startup for a (task, node), or None."""
        profile = self.get_profile(task_template_id)
        if profile and node_id in profile.metrics_by_node:
            m = profile.metrics_by_node[node_id]
            if m.count > 0:
                return m.total_cost
        return None

    def get_failure_rate(self, task_template_id: str, node_id: str) -> float:
        profile = self.get_profile(task_template_id)
        if profile:
            return profile.get_failure_rate(node_id)
        return 0.0

    def record_failure(self, task_template_id: str, node_id: str):
        if task_template_id not in self.profiles:
            self.profiles[task_template_id] = TaskProfile(task_template_id=task_template_id)
        self.profiles[task_template_id].record_failure(node_id)

    def record_observation(self, task_template_id: str, node_id: str,
                           node_type: NodeType, actual_runtime: float,
                           actual_startup: float, node_cpu_at_start: float = 0.0,
                           node_memory_at_start: float = 0.0,
                           io_bytes_read: int = 0,
                           io_bytes_written: int = 0,
                           output_bytes_by_field: Optional[Dict[str, int]] = None,
                           temperature_at_start: Optional[float] = None,
                           temperature_at_end: Optional[float] = None,
                           transfer_seconds: float = 0.0):
        """Records a runtime observation for a (task, node) pair and recomputes rankings.

        Phase 0 keeps the public signature backward-compatible: every caller
        in the existing engine/observer/test code keeps working unchanged,
        new fields are opt-in keyword arguments populated by the richer
        observers introduced in later phases."""
        if task_template_id not in self.profiles:
            self.profiles[task_template_id] = TaskProfile(task_template_id=task_template_id)

        profile = self.profiles[task_template_id]

        # Register the node_id → NodeType mapping
        profile._node_type_map[node_id] = node_type

        # Ensure per-node metrics exist
        if node_id not in profile.metrics_by_node:
            profile.metrics_by_node[node_id] = NodeMetrics()

        # Add the observation (rolling window, median computed on demand)
        profile.metrics_by_node[node_id].add_observation(Observation(
            runtime=actual_runtime,
            startup=actual_startup,
            node_cpu_at_start=node_cpu_at_start,
            node_memory_at_start=node_memory_at_start,
            io_bytes_read=io_bytes_read,
            io_bytes_written=io_bytes_written,
            output_bytes_by_field=dict(output_bytes_by_field or {}),
            temperature_at_start=temperature_at_start,
            temperature_at_end=temperature_at_end,
            transfer_seconds=transfer_seconds,
            timestamp=time.time(),
        ))

        # Recompute type-level aggregates and rankings
        profile.update_preferences()

    # ------------------------------------------------------------------
    # New helpers used by the upcoming AdaptivePolicy / ECT calculator.
    # Phase 0 only exposes them; legacy code paths do not call them yet.
    # ------------------------------------------------------------------
    def get_ucb_score(self, task_template_id: str, node_id: str,
                     beta: float = 1.0) -> Optional[float]:
        """Lower-confidence expected cost (lower = better). None if no data."""
        profile = self.get_profile(task_template_id)
        if profile and node_id in profile.metrics_by_node:
            m = profile.metrics_by_node[node_id]
            if m.count > 0:
                return m.ucb_score(beta=beta)
        return None

    def decay_failures(self, half_life_hours: float = 14.0,
                       now_ts: Optional[float] = None):
        """Run an exponential-decay pass on every profile's failure counters.
        Idempotent and cheap; safe to call once per tick on the heavy path."""
        ts = now_ts if now_ts is not None else time.time()
        for profile in self.profiles.values():
            profile.decay_failures(ts, half_life_hours=half_life_hours)

    def is_node_drifting(self, task_template_id: str, node_id: str,
                         drift_threshold: float = 0.25) -> bool:
        """True if the EMA runtime for this (task, node) has diverged from
        the rolling median. AdaptivePolicy uses this to force exploration."""
        profile = self.get_profile(task_template_id)
        if not profile or node_id not in profile.metrics_by_node:
            return False
        return profile.metrics_by_node[node_id].is_drifting(drift_threshold)

    # ------------------------------------------------------------------
    # Thermal-bucketed predictions (Phase 5)
    # ------------------------------------------------------------------
    def get_bucketed_runtime(self, task_template_id: str, node_id: str,
                             thermal_bucket: str = "warm") -> Optional[float]:
        """
        Return the median runtime for observations whose temperature bucket
        matches ``thermal_bucket``. Falls back to the unbucketed median if
        there are fewer than 2 matching observations.

        Thermal bucket labels: "cold", "warm", "hot", "throttle".
        """
        from services.thermal import ThermalCollector

        profile = self.get_profile(task_template_id)
        if not profile or node_id not in profile.metrics_by_node:
            return None
        m = profile.metrics_by_node[node_id]
        if m.count == 0:
            return None

        # Filter observations by thermal bucket.
        matching = []
        for obs in m.observations:
            bucket = ThermalCollector.thermal_bucket(obs.temperature_at_start)
            if bucket == thermal_bucket:
                matching.append(obs.runtime)

        if len(matching) >= 2:
            from statistics import median as _median
            return _median(matching)
        # Fall back to unbucketed median.
        return m.median_runtime

    # ------------------------------------------------------------------
    # Serialisation — JSON export / import for persistence
    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialise the entire store to a JSON string."""
        data = {
            "_schema_version": PROFILE_STORE_SCHEMA_VERSION,
            "profiles": {},
        }
        for tid, profile in self.profiles.items():
            p = {
                "node_type_map": {nid: nt.name for nid, nt in profile._node_type_map.items()},
                "failures_by_node": {nid: float(c) for nid, c in profile.failures_by_node.items()},
                "failures_last_decay_ts": profile.failures_last_decay_ts,
                "nodes": {},
            }
            for nid, nm in profile.metrics_by_node.items():
                p["nodes"][nid] = {
                    "ema_runtime": nm.ema_runtime,
                    "observations": [
                        {
                            "runtime": o.runtime,
                            "startup": o.startup,
                            "cpu": o.node_cpu_at_start,
                            "mem": o.node_memory_at_start,
                            "io_r": o.io_bytes_read,
                            "io_w": o.io_bytes_written,
                            "out": o.output_bytes_by_field,
                            "t_s": o.temperature_at_start,
                            "t_e": o.temperature_at_end,
                            "xfer": o.transfer_seconds,
                            "ts": o.timestamp,
                        }
                        for o in nm.observations
                    ],
                }
            data["profiles"][tid] = p
        return json.dumps(data)

    def load_json(self, raw: str):
        """Restore profiles from a JSON string (additive — merges with existing data).

        Accepts both v1 (flat dict of profiles) and v2 (versioned envelope)
        layouts so on-disk state from earlier deployments still loads.
        """
        raw_data = json.loads(raw)
        if isinstance(raw_data, dict) and "_schema_version" in raw_data:
            data = raw_data.get("profiles", {})
        else:
            data = raw_data  # legacy v1: dict[tid -> profile_dict]

        for tid, p in data.items():
            if tid not in self.profiles:
                self.profiles[tid] = TaskProfile(task_template_id=tid)
            profile = self.profiles[tid]

            # Restore node type map
            for nid, nt_name in p.get("node_type_map", {}).items():
                profile._node_type_map[nid] = NodeType[nt_name]

            # Restore failure counters (may be int from v1 or float from v2)
            for nid, count in p.get("failures_by_node", {}).items():
                profile.failures_by_node[nid] = float(count)
            profile.failures_last_decay_ts = float(
                p.get("failures_last_decay_ts", 0.0))

            # Restore per-node observations — v2 wraps them in {ema_runtime,
            # observations}, v1 was a flat list per node_id.
            nodes_blob = p.get("nodes", {})
            for nid, payload in nodes_blob.items():
                if nid not in profile.metrics_by_node:
                    profile.metrics_by_node[nid] = NodeMetrics()
                target = profile.metrics_by_node[nid]
                if isinstance(payload, list):
                    obs_list = payload  # legacy
                    target.ema_runtime = None
                else:
                    obs_list = payload.get("observations", [])
                    target.ema_runtime = payload.get("ema_runtime")
                for o in obs_list:
                    target.add_observation(Observation(
                        runtime=o["runtime"],
                        startup=o["startup"],
                        node_cpu_at_start=o.get("cpu", 0.0),
                        node_memory_at_start=o.get("mem", 0.0),
                        io_bytes_read=int(o.get("io_r", 0)),
                        io_bytes_written=int(o.get("io_w", 0)),
                        output_bytes_by_field=dict(o.get("out", {}) or {}),
                        temperature_at_start=o.get("t_s"),
                        temperature_at_end=o.get("t_e"),
                        transfer_seconds=float(o.get("xfer", 0.0)),
                        timestamp=o.get("ts", 0.0),
                    ))
            profile.update_preferences()