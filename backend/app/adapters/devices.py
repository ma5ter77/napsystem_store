"""DeviceAdapter contract, SimulatedDeviceAdapter, web-headphones controller and a NAP chair placeholder.

Real chair commands are NOT invented: without the manufacturer protocol the NapChairAdapter refuses every call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class DeviceProtocolUnavailable(Exception):
    pass


@dataclass
class Capabilities:
    model: str
    api_version: str
    firmware: str
    sample_rates: list[int]
    audio_outputs: list[str]
    channel_separation: str  # "verified_hardware" | "user_confirmation_required" | "none"
    sensors: list[str]
    features: list[str]
    limits: dict = field(default_factory=dict)
    simulated: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def provides(self) -> set[str]:
        caps = {"stereo_output"} if "stereo" in self.audio_outputs or "headphones" in self.audio_outputs else set()
        if self.channel_separation == "verified_hardware":
            caps.add("stereo_separation")
        if "vibroacoustic" in self.audio_outputs:
            caps.add("vibroacoustic_output")
        if "heart_rate" in self.sensors:
            caps.add("heart_rate_sensor")
        if "hrv" in self.sensors:
            caps.add("hrv_sensor")
        return caps


class DeviceAdapter(Protocol):
    name: str
    def connect(self, device_id: str) -> None: ...
    def disconnect(self, device_id: str) -> None: ...
    def get_capabilities(self, device_id: str) -> Capabilities: ...
    def prepare_session(self, device_id: str, session: dict) -> dict: ...
    def start(self, device_id: str, session_id: str) -> dict: ...
    def pause(self, device_id: str, session_id: str) -> dict: ...
    def resume(self, device_id: str, session_id: str) -> dict: ...
    def stop(self, device_id: str, session_id: str) -> dict: ...
    def get_status(self, device_id: str) -> dict: ...


SIM_PROFILES = {
    "sim-chair-v1": Capabilities("NAP-SIM-CHAIR", "1.0", "sim-0.1", [48000], ["headphones", "vibroacoustic"],
                                 "verified_hardware", ["heart_rate", "hrv"], ["pause", "resume", "local_stop"],
                                 {"max_volume_dbfs": -10}, True),
    "sim-speakers": Capabilities("NAP-SIM-SPEAKERS", "1.0", "sim-0.1", [44100, 48000], ["stereo"], "none", [],
                                 ["pause", "resume", "local_stop"], {}, True),
    "sim-legacy-api": Capabilities("NAP-SIM-LEGACY", "0.9", "sim-0.0", [48000], ["headphones"], "verified_hardware",
                                   [], ["local_stop"], {}, True),
}

WEB_HEADPHONES = Capabilities("WEB-PLAYER", "1.0", "browser", [48000], ["headphones"], "user_confirmation_required",
                              [], ["pause", "resume", "local_stop"], {}, False)


class SimulatedDeviceAdapter:
    """In-memory device used for demos and tests. Clearly marked as a simulator."""
    name = "simulated"

    def __init__(self):
        self.state: dict[str, dict] = {}

    def connect(self, device_id):
        self.state.setdefault(device_id, {"connected": True, "session": None, "status": "idle"})
        self.state[device_id]["connected"] = True

    def disconnect(self, device_id):
        self.state.setdefault(device_id, {})["connected"] = False

    def get_capabilities(self, device_id, profile="sim-chair-v1"):
        return SIM_PROFILES[profile]

    def prepare_session(self, device_id, session):
        self.connect(device_id)
        self.state[device_id].update(session=session["id"], status="ready")
        return {"accepted": True, "simulated": True}

    def _set(self, device_id, session_id, status):
        st = self.state.setdefault(device_id, {"connected": True, "session": session_id})
        st.update(session=session_id, status=status)
        return {"accepted": True, "simulated": True, "status": status}

    def start(self, device_id, session_id):
        return self._set(device_id, session_id, "running")

    def pause(self, device_id, session_id):
        return self._set(device_id, session_id, "paused")

    def resume(self, device_id, session_id):
        return self._set(device_id, session_id, "running")

    def stop(self, device_id, session_id):
        return self._set(device_id, session_id, "stopped")

    def get_status(self, device_id):
        return dict(self.state.get(device_id, {"connected": False}), simulated=True)


class WebPlayerController(SimulatedDeviceAdapter):
    """The browser player is the local controller; the server records commands/events it reports."""
    name = "web_headphones"

    def get_capabilities(self, device_id, profile=None):
        return WEB_HEADPHONES


class NapChairAdapter:
    name = "nap_chair"

    def _no(self, *a, **k):
        raise DeviceProtocolUnavailable("NAP SYSTEM chair protocol documentation is not available")

    connect = disconnect = get_capabilities = prepare_session = start = pause = resume = stop = get_status = _no


_sim = SimulatedDeviceAdapter()
_web = WebPlayerController()


def adapter_for(name: str) -> DeviceAdapter:
    return {"simulated": _sim, "web_headphones": _web, "nap_chair": NapChairAdapter()}[name]
