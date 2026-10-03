from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config" / "litdb"
DEFAULT_HOME = PROJECT_ROOT / "data" / "literature-db"
ACCEPTANCE_VERSION = "browser-v2"
# The v2 acceptance baseline is 106 venues (46 conferences + 60 journals).
# ICCD and ISCAS were explicitly added to the user's requested EDA scope;
# Integration, the VLSI Journal was later retired from the executable scope
# at the user's request. Keep both historical facts in history while
# validating the current 107-venue / 48-conference registry.
EXPECTED_VENUES = 107
EXPECTED_CONFERENCES = 48
EXPECTED_JOURNALS = 59
YEAR_FROM = 2015

API_KEY_NAMES = (
    "OPENAI_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    "OPENALEX_API_KEY",
    "IEEE_API_KEY",
    "SPRINGER_API_KEY",
    "ELSEVIER_API_KEY",
    "CROSSREF_API_KEY",
)

VENUE_STATES = {
    "UNSEEN",
    "DISCOVERY_RUNNING",
    "RECIPE_CANDIDATE",
    "PILOT_RUNNING",
    "PILOT_PASSED",
    "REPLAY_RUNNING",
    "RECIPE_LOCKED",
    "COUNT_RUNNING",
    "COUNT_BASELINED",
    "BOOTSTRAP_RUNNING",
    "BOOTSTRAP_STAGED",
    "RECONCILING",
    "ACTIVE",
    "AUTH_REQUIRED",
    "RECIPE_DRIFT",
    "SOURCE_BLOCKED",
    "POLICY_BLOCKED",
    "PARTIAL",
    "FAILED",
}

TRANSITIONS = {
    "UNSEEN": {"DISCOVERY_RUNNING"},
    "DISCOVERY_RUNNING": {"RECIPE_CANDIDATE", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED", "PARTIAL", "FAILED"},
    "RECIPE_CANDIDATE": {"PILOT_RUNNING", "DISCOVERY_RUNNING", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED"},
    "PILOT_RUNNING": {"PILOT_PASSED", "PARTIAL", "RECIPE_DRIFT", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED", "FAILED"},
    "PILOT_PASSED": {"REPLAY_RUNNING"},
    "REPLAY_RUNNING": {"RECIPE_LOCKED", "DISCOVERY_RUNNING", "PARTIAL", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED", "FAILED"},
    "RECIPE_LOCKED": {"COUNT_RUNNING", "RECIPE_DRIFT"},
    "COUNT_RUNNING": {"COUNT_BASELINED", "PARTIAL", "RECIPE_DRIFT", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED", "FAILED"},
    "COUNT_BASELINED": {"BOOTSTRAP_RUNNING", "RECIPE_DRIFT"},
    "BOOTSTRAP_RUNNING": {"BOOTSTRAP_STAGED", "PARTIAL", "RECIPE_DRIFT", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED", "FAILED"},
    "BOOTSTRAP_STAGED": {"RECONCILING"},
    "RECONCILING": {"ACTIVE", "PARTIAL", "RECIPE_DRIFT", "FAILED"},
    "ACTIVE": {"RECIPE_DRIFT", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_BLOCKED"},
    "AUTH_REQUIRED": {"DISCOVERY_RUNNING", "PILOT_RUNNING", "COUNT_RUNNING", "BOOTSTRAP_RUNNING"},
    "RECIPE_DRIFT": {"DISCOVERY_RUNNING"},
    "PARTIAL": {"DISCOVERY_RUNNING", "PILOT_RUNNING", "COUNT_RUNNING", "BOOTSTRAP_RUNNING", "RECONCILING"},
    "FAILED": {"DISCOVERY_RUNNING"},
    # A source block can be environmental (for example, a browser-client
    # network block) and may later be cleared without invalidating already
    # accepted artifacts. Resume only through a gated running phase; the
    # controller still needs an artifact-scoped receipt for the transition.
    "SOURCE_BLOCKED": {
        "DISCOVERY_RUNNING", "PILOT_RUNNING", "REPLAY_RUNNING",
        "COUNT_RUNNING", "BOOTSTRAP_RUNNING",
    },
    "POLICY_BLOCKED": set(),
}
