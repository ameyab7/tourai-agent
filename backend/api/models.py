"""api/models.py — Pydantic request / response models."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class VisiblePoisRequest(BaseModel):
    latitude:  float = Field(..., ge=-90,  le=90,   description="WGS84 latitude")
    longitude: float = Field(..., ge=-180, le=180,  description="WGS84 longitude")
    heading:   float = Field(..., ge=0,    lt=360,  description="Compass heading in degrees (0=N, 90=E)")
    radius:    float = Field(300.0, gt=0,  le=1000, description="Search radius in metres")


class PoiOut(BaseModel):
    id:         Any
    name:       str
    lat:        float
    lon:        float
    poi_type:   str
    distance_m: float
    angle_deg:  float
    tags:       dict[str, Any] = {}


class VisiblePoisResponse(BaseModel):
    visible_pois:   list[PoiOut]
    rejected_pois:  list[PoiOut] = []
    street_name:    str | None
    total_checked:  int
    cache_hit:      bool
    correlation_id: str
    timestamp:      str


class CurrentStreetResponse(BaseModel):
    street_name: str | None
    latitude:    float
    longitude:   float


class AskRequest(BaseModel):
    question:  str   = Field(..., min_length=1, max_length=500)
    latitude:  float = Field(..., ge=-90,  le=90)
    longitude: float = Field(..., ge=-180, le=180)
    context:   dict[str, Any] = {}


class AskResponse(BaseModel):
    answer:         str
    question:       str
    correlation_id: str


class StoryRequest(BaseModel):
    poi_id:    str
    poi_name:  str
    poi_type:  str
    tags:      dict[str, Any] = {}
    latitude:  float = Field(..., ge=-90,  le=90)
    longitude: float = Field(..., ge=-180, le=180)


class StoryResponse(BaseModel):
    poi_id:         str
    story:          str
    cached:         bool
    correlation_id: str


class DependencyStatus(BaseModel):
    name:   str
    ok:     bool
    detail: str = ""


class HealthResponse(BaseModel):
    status:       str
    dependencies: list[DependencyStatus]
    timestamp:    str


# ---------------------------------------------------------------------------
# Feedback models
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Profile / onboarding models
# ---------------------------------------------------------------------------

class ProfileSetupRequest(BaseModel):
    device_id:           str | None = None
    interests:           list[str]  = Field(..., min_length=1)
    travel_style:        str        = Field(..., pattern="^(solo|couple|family|group)$")
    pace:                str        = Field(..., pattern="^(relaxed|balanced|packed)$")
    drive_tolerance_hrs: float      = Field(..., ge=0, le=6)


class ProfileSetupResponse(BaseModel):
    status:    str   # "created" | "updated"
    user_id:   str
    device_id: str | None
    timestamp: str


class ProfileGetResponse(BaseModel):
    user_id:             str
    device_id:           str | None
    interests:           list[str]
    travel_style:        str
    pace:                str
    drive_tolerance_hrs: float
    created_at:          str
    updated_at:          str


class FeedbackRequest(BaseModel):
    """Report a false positive or false negative from the visibility filter."""
    latitude:    float = Field(..., ge=-90,  le=90)
    longitude:   float = Field(..., ge=-180, le=180)
    heading:     float = Field(..., ge=0,    lt=360)
    poi_id:      Any
    poi_name:    str   = Field(..., min_length=1)
    poi_lat:     float = Field(..., ge=-90,  le=90)
    poi_lon:     float = Field(..., ge=-180, le=180)
    poi_tags:    dict[str, Any] = {}
    poi_geometry: list[dict[str, Any]] = []
    # What the user observed
    user_says:   str   = Field(..., pattern="^(YES|NO)$",
                               description="YES = I can see it, NO = I cannot see it")
    user_street: str | None = None
    note:        str | None = None


class FeedbackDiagnosis(BaseModel):
    poi_id:           Any
    poi_name:         str
    distance_m:       float
    bearing_deg:      float
    angle_deg:        float
    in_fov:           bool
    size:             str
    rule:             str
    rule_description: str
    visible:          bool
    confidence:       float
    filter_now_says:  str
    already_fixed:    bool
    agreement:        str   # "AGREE" | "DISAGREE"


class FeedbackResponse(BaseModel):
    status:         str          # "logged"
    diagnosis:      FeedbackDiagnosis
    correlation_id: str
    timestamp:      str


# ---------------------------------------------------------------------------
# Itinerary models
# ---------------------------------------------------------------------------

class ItineraryRequest(BaseModel):
    destination:         str   = Field(..., min_length=2, max_length=200)
    start_date:          str   = Field(..., description="ISO date YYYY-MM-DD")
    end_date:            str   = Field(..., description="ISO date YYYY-MM-DD")
    # Optional user preferences (override profile if provided)
    interests:           list[str] = []
    travel_style:        str | None = None
    pace:                str | None = None
    drive_tolerance_hrs: float | None = None


class ItineraryStop(BaseModel):
    name:                str
    poi_type:            str
    tip:                 str
    arrival_time:        str
    duration_min:        int
    drive_from_prev_min: int
    overnight_warning:   bool = False


class ItineraryDay(BaseModel):
    date:      str
    day_label: str
    stops:     list[ItineraryStop]


class ItineraryResponse(BaseModel):
    title:          str
    summary:        str
    destination:    str
    start_date:     str
    end_date:       str
    days:           list[ItineraryDay]
    correlation_id: str


class ReplanRequest(BaseModel):
    reason:          Literal["bad_weather", "running_late", "tired", "place_closed", "free_text"]
    day_index:       int
    from_stop_index: int | None = None
    free_text:       str | None = Field(None, max_length=500)
    closed_poi_ids:  list[str] = []
