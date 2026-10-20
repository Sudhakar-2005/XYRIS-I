"""
memory_holo — the Living Memory Brain's view half (Stage A, approved Option 4).

A read-only loopback visualization of XYRIS I's real memory using a derived
copy of the MIT holo-gestures page:

  * ``memory_holo/adapter.py``   pure mapping: memory_manager → HOLO tree/stats
  * ``memory_holo/server.py``    stdlib HTTP on 127.0.0.1:4891 (fail-soft)
  * ``memory_holo/memory_view.html``  derived page (camera/three.js removed)

Rules recorded in docs/living-memory-brain/STAGE_A_AUDIT.md: the memory
backend stays the source of truth, the view never writes, nothing shown is
invented, and no new heavy dependencies may be added.
"""

__all__ = ["adapter", "server"]
