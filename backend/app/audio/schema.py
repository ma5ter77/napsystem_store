"""Project state, logical buses and routing profiles (spec v2.1 §3, §5).

All clip timing is in integer sample frames at the project master rate; the UI shows seconds.
Nothing here implies processing: gain is 0 dB, fades are 0 frames and there is no SRC unless stated.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BUSES = ("NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER", "MUSIC_LEFT", "MUSIC_RIGHT", "LFE", "BACK_LEFT", "BACK_RIGHT")
SAMPLE_RATES = (44_100, 48_000, 88_200, 96_000)
BIT_DEPTHS = (16, 24, 32)  # 32 = IEEE float
MAX_DURATION_FRAMES_PER_HOUR = 3600
MAX_TRACKS = 64
MAX_CLIPS = 2000

# WAVE_FORMAT_EXTENSIBLE speaker bits; PCM order inside the file follows the bit order of the mask
SPEAKER = {"FL": 0x1, "FR": 0x2, "FC": 0x4, "LFE": 0x8, "BL": 0x10, "BR": 0x20, "SL": 0x200, "SR": 0x400}
G3DB = 0.7071067811865476

# Built-in profiles, version 1. Rows = output channels in file order, values = {bus: linear gain}.
# These are NAP conventions (spec §3), not a universal physical layout.
PROFILES: dict[str, dict] = {
    "HEADPHONES_2_0": {"name": "Наушники 2.0", "layout": "stereo", "channels": ["FL", "FR"], "matrix": [
        {"NEURO_LEFT": 1.0, "MUSIC_LEFT": 1.0, "VOICE_CENTER": G3DB, "BACK_LEFT": G3DB},
        {"NEURO_RIGHT": 1.0, "MUSIC_RIGHT": 1.0, "VOICE_CENTER": G3DB, "BACK_RIGHT": G3DB}],
        "note": "Neuro L/R stay on separate ears (no cross-feed), so their frequency difference survives; "
                "voice is centred at -3 dB per side; LFE is not sent to headphones."},
    "NAP_ACOUSTIC_3_0": {"name": "Акустика NAP 3.0", "layout": "3.0", "channels": ["FL", "FR", "FC"], "matrix": [
        {"NEURO_LEFT": 1.0, "MUSIC_LEFT": 1.0, "BACK_LEFT": G3DB},
        {"NEURO_RIGHT": 1.0, "MUSIC_RIGHT": 1.0, "BACK_RIGHT": G3DB},
        {"VOICE_CENTER": 1.0}],
        "note": "3.0 is left/right/centre — not 2.1. Voice in the centre; neuro L/R with the music on L/R."},
    "NAP_CHAIR_5_1": {"name": "Кресло NAP 5.1", "layout": "5.1(side)", "channels": ["FL", "FR", "FC", "LFE", "SL", "SR"],
                      "matrix": [{"MUSIC_LEFT": 1.0}, {"MUSIC_RIGHT": 1.0}, {"VOICE_CENTER": 1.0}, {"LFE": 1.0},
                                 {"NEURO_LEFT": 1.0}, {"NEURO_RIGHT": 1.0}],
                      "note": "FL=MUSIC_LEFT, FR=MUSIC_RIGHT, FC=VOICE_CENTER, LFE=LFE, SL=NEURO_LEFT, SR=NEURO_RIGHT."},
    "NAP_PRO_7_1": {"name": "NAP Pro 7.1", "layout": "7.1", "channels": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
                    "matrix": [{"MUSIC_LEFT": 1.0}, {"MUSIC_RIGHT": 1.0}, {"VOICE_CENTER": 1.0}, {"LFE": 1.0},
                               {"BACK_LEFT": 1.0}, {"BACK_RIGHT": 1.0}, {"NEURO_LEFT": 1.0}, {"NEURO_RIGHT": 1.0}],
                    "note": "FL, FR, FC, LFE, BL, BR, SL=NEURO_LEFT, SR=NEURO_RIGHT."},
}
DEFAULT_PROFILE_FOR_BUSES = {2: "HEADPHONES_2_0", 3: "NAP_ACOUSTIC_3_0", 6: "NAP_CHAIR_5_1", 8: "NAP_PRO_7_1"}
BUS_SETS = {2: ("NEURO_LEFT", "NEURO_RIGHT"), 3: ("NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER"),
            6: ("NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER", "MUSIC_LEFT", "MUSIC_RIGHT", "LFE"), 8: BUSES}


def channel_mask(profile: dict) -> int:
    m = 0
    for ch in profile["channels"]:
        m |= SPEAKER[ch]
    order = sorted(profile["channels"], key=lambda c: SPEAKER[c])
    if order != profile["channels"]:
        raise ValueError("profile channel order must follow the WAVE_FORMAT_EXTENSIBLE bit order")
    return m


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(Model):
    frame: int = Field(ge=0)
    value: float


class BinauralParams(Model):
    carrier_hz: float = Field(gt=20, le=1500)
    delta_hz: float = Field(gt=0, le=100)
    amplitude_dbfs: float = Field(ge=-80, le=-1)
    phase_left_deg: float = 0.0
    phase_right_deg: float = 0.0
    ramp_frames: int = Field(default=0, ge=0)              # fade in/out of the generator itself (explicit)
    carrier_automation: list[Point] = Field(default_factory=list, max_length=256)  # linear, phase-continuous
    delta_automation: list[Point] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def _limits(self):
        for pts, lo, hi in ((self.carrier_automation, 20, 1500), (self.delta_automation, 0.0001, 100)):
            if any(b.frame <= a.frame for a, b in zip(pts, pts[1:])):
                raise ValueError("automation frames must increase")
            if any(not (lo <= p.value <= hi) for p in pts):
                raise ValueError("automation value out of range")
        return self


class IsochronicParams(Model):
    carrier_hz: float = Field(gt=20, le=8000)
    rate_hz: float = Field(gt=0, le=40)
    duty: float = Field(default=0.5, gt=0.05, lt=0.95)
    edge_ms: float = Field(default=5.0, ge=1.0, le=50.0)    # raised-cosine edges: no clicks, band-limited envelope
    amplitude_dbfs: float = Field(ge=-80, le=-1)
    ramp_frames: int = Field(default=0, ge=0)


class Generator(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    type: Literal["binaural", "isochronic"]
    version: int = 1
    params: dict

    @model_validator(mode="after")
    def _params(self):
        (BinauralParams if self.type == "binaural" else IsochronicParams).model_validate(self.params)
        return self

    def parsed(self):
        return (BinauralParams if self.type == "binaural" else IsochronicParams).model_validate(self.params)


class Clip(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    start_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)
    asset_id: str | None = None          # immutable studio source asset
    source_channel: int = Field(default=0, ge=0, le=7)
    source_offset_frame: int = Field(default=0, ge=0)
    generator_id: str | None = None      # or a generator node
    generator_output: Literal["left", "right", "mono"] = "mono"
    gain_db: float = Field(default=0.0, ge=-96, le=24)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    mute: bool = False

    @model_validator(mode="after")
    def _source(self):
        if (self.asset_id is None) == (self.generator_id is None):
            raise ValueError("a clip needs exactly one of asset_id or generator_id")
        if self.fade_in_frames + self.fade_out_frames > self.duration_frames:
            raise ValueError("fades are longer than the clip")
        return self


class Track(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(default="", max_length=80)
    bus: str
    mute: bool = False
    solo: bool = False
    clips: list[Clip] = Field(default_factory=list)

    @field_validator("bus")
    @classmethod
    def _bus(cls, v):
        if v not in BUSES:
            raise ValueError("unknown bus")
        return v


class Output(Model):
    on_clip: Literal["fail", "hard_clip"] = "fail"   # never a hidden limiter (spec §2.5)


class ProjectState(Model):
    tracks: list[Track] = Field(default_factory=list, max_length=MAX_TRACKS)
    generators: list[Generator] = Field(default_factory=list, max_length=64)
    output: Output = Field(default_factory=Output)

    @model_validator(mode="after")
    def _refs(self):
        gens = {g.id for g in self.generators}
        if len(gens) != len(self.generators):
            raise ValueError("duplicate generator id")
        ids = [t.id for t in self.tracks] + [c.id for t in self.tracks for c in t.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate track/clip id")
        if sum(len(t.clips) for t in self.tracks) > MAX_CLIPS:
            raise ValueError("too many clips")
        for t in self.tracks:
            for c in t.clips:
                if c.generator_id and c.generator_id not in gens:
                    raise ValueError(f"clip {c.id}: unknown generator")
        return self

    def duration_frames(self) -> int:
        return max((c.start_frame + c.duration_frames for t in self.tracks for c in t.clips), default=0)

    def asset_ids(self) -> set[str]:
        return {c.asset_id for t in self.tracks for c in t.clips if c.asset_id}


def canonical(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def state_hash(state: dict) -> str:
    return hashlib.sha256(canonical(state).encode()).hexdigest()
