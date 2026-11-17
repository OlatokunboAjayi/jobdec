"""
Configurable Behavioral Templates & Candidate Profile Rules (Feature 15, R3).
Loads behavioral rules, STAR narrative templates, SJT leadership principles,
and Likert trait polarities from .env and candidate profile.json.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from candidate import fact, standing_facts


DEFAULT_AMBIGUITY_STAR = (
    "During a complex automation project with ambiguous requirements across multiple stakeholders, "
    "my task was to design and deploy a reliable pipeline without manual data entry or data loss. "
    "I translated fragmented requirements into unified technical specs and built a Python validation "
    "engine with pre-submission checks and live telemetry dashboards. The automated system processed "
    "records cleanly, reduced manual coordination overhead, and eliminated runtime errors."
)

DEFAULT_CONFLICT_STAR = (
    "When teammates disagreed on implementation language and architecture tradeoffs, I reframed the "
    "discussion around functional requirements rather than personal preference. I ran a quick benchmark "
    "and proposed a decoupled interface boundary so each component used the best-fit stack. Both sides "
    "saw their priorities respected, and we shipped on schedule with zero race conditions."
)

DEFAULT_FAILURE_STAR = (
    "When an early pipeline iteration experienced intermittent state desynchronization under schema "
    "changes, I paused execution, isolated the ingestion boundary, and added automated regression tests "
    "with strict schema validation and circuit breakers. This caught malformed payloads in pre-flight "
    "checks and eliminated runtime exceptions."
)

DEFAULT_LEADERSHIP_STAR = (
    "While leading engineering automation initiatives, I aligned technical and non-technical stakeholders "
    "by translating operational bottlenecks into measurable software milestones, reproducible validation "
    "pipelines, and clear audit trackers."
)


@dataclass
class BehavioralProfileConfig:
    """Strongly-typed behavioral configuration loaded dynamically from .env and candidate facts."""
    candidate_name: str = "Candidate"
    university: str = "University"
    degree: str = "Bachelor's Degree"
    gpa: str = ""
    top_skills: List[str] = field(default_factory=list)
    elevator_pitch: str = ""
    proudest_achievement: str = ""
    framework: str = "STAR"
    tone: str = "professional, authentic, concise, high ownership, quantifiable impact"
    core_values: List[str] = field(default_factory=list)
    role_fit: str = "Forward Deployed Engineer / AI Systems / Product & Data"
    star_templates: Dict[str, str] = field(default_factory=dict)
    trait_polarities: Dict[str, int] = field(default_factory=dict)
    standing_facts: List[str] = field(default_factory=list)


def load_behavioral_config() -> BehavioralProfileConfig:
    """Loads behavioral templates and candidate profile rules from current environment."""
    first = fact("FIRST_NAME", "")
    last = fact("LAST_NAME", "")
    full_name = os.getenv("CANDIDATE_NAME") or fact("FULL_NAME") or (f"{first} {last}".strip() or "Candidate")
    university = fact("UNIVERSITY", "University") or "University"
    degree = fact("DEGREE_EXACT") or fact("DEGREE", "Bachelor's Degree")
    gpa = fact("GPA", "")

    skills_raw = fact(
        "TOP_SKILLS",
        "Python, SQL, Process Automation, Agentic AI, Claude API, Playwright, Data Validation, Git",
    )
    top_skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

    gpa_str = f" ({gpa} GPA)" if gpa else ""
    elevator_pitch = fact(
        "ELEVATOR_PITCH",
        f"Student at {university}{gpa_str} specializing in Python automation, "
        "multi-agent AI systems, and high-reliability data pipelines.",
    )
    proudest_achievement = fact("PROUDEST_ACHIEVEMENT", DEFAULT_AMBIGUITY_STAR)

    framework = os.getenv("ASSESSMENT_BEHAVIORAL_FRAMEWORK", "STAR").strip() or "STAR"
    tone = os.getenv(
        "ASSESSMENT_BEHAVIORAL_TONE",
        "professional, authentic, concise, high ownership, quantifiable impact",
    ).strip()
    values_raw = os.getenv(
        "ASSESSMENT_BEHAVIORAL_CORE_VALUES",
        "Ownership, Customer Obsession, Bias for Action, Technical Rigor, Collaborative Empathy, Data Integrity",
    )
    core_values = [v.strip() for v in values_raw.split(",") if v.strip()]
    role_fit = os.getenv(
        "ASSESSMENT_BEHAVIORAL_ROLE_FIT",
        "Software Engineer / AI Systems Developer / Quantitative Technologist",
    ).strip()

    star_templates = {
        "ambiguity_process_automation": os.getenv("ASSESSMENT_TEMPLATE_AMBIGUITY", DEFAULT_AMBIGUITY_STAR).strip() or DEFAULT_AMBIGUITY_STAR,
        "conflict_architecture_disagreement": os.getenv("ASSESSMENT_TEMPLATE_CONFLICT", DEFAULT_CONFLICT_STAR).strip() or DEFAULT_CONFLICT_STAR,
        "failure_bug_learning": os.getenv("ASSESSMENT_TEMPLATE_FAILURE_LEARNING", DEFAULT_FAILURE_STAR).strip() or DEFAULT_FAILURE_STAR,
        "leadership_ownership": os.getenv("ASSESSMENT_TEMPLATE_LEADERSHIP", DEFAULT_LEADERSHIP_STAR).strip() or DEFAULT_LEADERSHIP_STAR,
    }

    # Default Likert target scores (1 = Strongly Disagree, 5 = Strongly Agree)
    trait_polarities = {
        "unit_testing_rigor": 5,
        "proactive_ownership": 5,
        "peer_feedback_humility": 5,
        "collaborative_teamwork": 5,
        "ethical_integrity": 5,
        "wait_for_instructions_passive": 1,
        "skip_testing_for_speed": 1,
        "blame_others_or_hide_bugs": 1,
        "work_in_isolation_avoid_peers": 1,
    }

    return BehavioralProfileConfig(
        candidate_name=full_name,
        university=university,
        degree=degree,
        gpa=gpa,
        top_skills=top_skills,
        elevator_pitch=elevator_pitch,
        proudest_achievement=proudest_achievement,
        framework=framework,
        tone=tone,
        core_values=core_values,
        role_fit=role_fit,
        star_templates=star_templates,
        trait_polarities=trait_polarities,
        standing_facts=standing_facts(),
    )


def get_behavioral_questions_catalog() -> List[Dict[str, str]]:
    """
    Returns the standard catalog of behavioral archetype questions and their .env configuration keys
    so the candidate can review or customize rules in .env at any time.
    """
    return [
        {
            "env_key": "ASSESSMENT_TEMPLATE_AMBIGUITY",
            "question": "Describe a situation where you had to automate or optimize a critical process under ambiguous requirements. How did you ensure reliability and what was the quantifiable impact?",
            "default_focus": "Delta Air Lines shift-bidding Python automation (50,000+ bids, 3,000+ records validated, 150+ hours saved).",
        },
        {
            "env_key": "ASSESSMENT_TEMPLATE_CONFLICT",
            "question": "Tell me about a time when you experienced a technical disagreement with teammates on architecture or tool selection. How did you resolve it?",
            "default_focus": "GSU Hacklanta Vantrell AI platform (20-min empirical benchmark, decoupled Python strategy agents + Rust execution layer).",
        },
        {
            "env_key": "ASSESSMENT_TEMPLATE_FAILURE_LEARNING",
            "question": "Describe a time when a system or code component you built failed or hit an unexpected edge case. How did you diagnose and prevent it from recurring?",
            "default_focus": "The Kalshi Desk (32 modules, 165 automated regression tests, strict Pydantic schema guards).",
        },
        {
            "env_key": "ASSESSMENT_TEMPLATE_LEADERSHIP",
            "question": "Give an example of when you took ownership of a cross-functional initiative to deliver results under a tight deadline.",
            "default_focus": "Translating frontline operational rules into deterministic Python specs with 6 operational telemetry trackers.",
        },
    ]
