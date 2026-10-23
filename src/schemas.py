"""Strict data contracts. Gemini output is only accepted if it matches IssueAnalysis exactly."""
from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    POTHOLE = "pothole_road_damage"
    GARBAGE = "garbage_waste"
    STREETLIGHT = "streetlight_electrical"
    WATER = "water_leak_drainage"
    TREE = "fallen_tree_obstruction"
    OTHER = "other"


class IssueAnalysis(BaseModel):
    category: Category
    severity: int = Field(ge=1, le=5, description="1 = minor nuisance, 5 = immediate danger to life")
    hazard: bool = Field(description="True if the issue poses an immediate safety risk to people")
    summary: str = Field(max_length=200, description="One neutral sentence describing the issue")
    confidence: float = Field(ge=0.0, le=1.0, description="Model's confidence in the category")


DEPARTMENTS = {
    Category.POTHOLE: "Roads & Public Works",
    Category.GARBAGE: "Solid Waste Management",
    Category.STREETLIGHT: "Electrical / Street Lighting",
    Category.WATER: "Water Supply & Drainage",
    Category.TREE: "Garden & Tree Authority",
    Category.OTHER: "General Grievance Cell",
}

STATUSES = ["Open", "In progress", "Resolved"]
