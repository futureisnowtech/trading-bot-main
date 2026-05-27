"""
runtime/ — Persistent system + lane runtime truth layer.

Modules:
  runtime_state       — system_runtime_state + lane_runtime_state tables
  lane_registry       — LaneRegistry: activation flags + startup callbacks
  incident_tracker    — incidents table: groups repeated errors
  position_reconciler — reconciles open_positions against trades ledger
  allocator           — cross-lane capital allocation scaffold (v16.0 TBD)
  economics           — unified lane-aware fee/edge interface
"""

__all__ = [
    "runtime_state",
    "lane_registry",
    "incident_tracker",
    "position_reconciler",
    "allocator",
    "economics",
]
