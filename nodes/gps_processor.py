# nodes/gps_processor.py
#
# Processes the user's GPS position and classifies their movement.
# In Phase 0, speed is hardcoded to 0.5 m/s (simulating walking).

import logging
from state import TourGuideState

logger = logging.getLogger(__name__)

# Speed thresholds in m/s
_STATIONARY_MAX = 0.3
_WALKING_MAX    = 2.0
_RUNNING_MAX    = 8.0


def _classify_speed(speed_mps: float) -> str:
    if speed_mps < _STATIONARY_MAX:
        return "stationary"
    elif speed_mps < _WALKING_MAX:
        return "walking"
    elif speed_mps < _RUNNING_MAX:
        return "running"
    return "driving"


async def gps_processor(state: TourGuideState) -> dict:
    # Phase 0: simulate walking speed
    speed_mps = 0.5
    mode = _classify_speed(speed_mps)

    logger.debug(
        "GPS: (%.6f, %.6f) speed=%.2f m/s mode=%s",
        state["latitude"], state["longitude"], speed_mps, mode,
    )

    return {"speed_mps": speed_mps}
