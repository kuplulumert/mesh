import pytest

from automesh.diagnostics.actions import Action, PLAN, SURFACE_REPAIR
from automesh.diagnostics.advisor import Advisor, _diagnosis_from_payload
from automesh.diagnostics.knowledge_base import RULES, RULES_BY_ID, diagnose
from automesh.diagnostics.repair import is_plan_change, repair_actions
from automesh.config import AdvisorSettings
from automesh.models import QualityReport, QualityVerdict


def test_every_rule_is_well_formed():
    assert len(RULES) >= 20
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    for rule in RULES:
        assert rule.patterns and rule.steps
        assert rule.explanation.strip()
        for step in rule.steps:
            assert step.actions


@pytest.mark.parametrize("text,stage,expected", [
    ("Error: There are 37 intersecting faces", "surface", "surface_intersecting_faces"),
    ("WARNING: 148 free faces found", "surface", "free_faces"),
    ("Error: prisms collapsed at 412 nodes", "boundary-layer", "prism_failure"),
    ("Error: Not enough memory to complete the operation", "any", "out_of_memory"),
    ("negative volume cells detected", "volume", "negative_volume"),
    ("Error: could not check out license", "any", "license"),
    ("Error: no such file or directory", "import", "file_missing"),
    ("multi-connected faces in zone wall", "surface", "multi_connected_faces"),
    ("leak path detected between regions", "any", "leakage"),
    ("hexcore generation failed", "volume", "hexcore_failure"),
])
def test_known_symptoms_are_recognised(text, stage, expected):
    found = diagnose(text, stage)
    assert expected in [d.rule_id for d in found]


def test_fatal_rules_short_circuit():
    found = diagnose("Error: license checkout failed and 12 free faces", "any")
    assert len(found) == 1
    assert found[0].is_fatal


def test_escalation_never_repeats_a_failed_remedy():
    occurrences = {}
    seen = []
    for _ in range(5):
        diagnosis = diagnose("prisms collapsed", "boundary-layer", occurrences)[0]
        occurrences[diagnosis.rule_id] = diagnosis.occurrence
        seen.append(tuple(a.operation for a in diagnosis.actions))
    # First four escalate; the fifth repeats the last rung and is flagged.
    assert len(set(seen[:4])) == 4
    assert diagnose("prisms collapsed", "boundary-layer", occurrences)[0].exhausted


def test_stage_filtering():
    assert not diagnose("There are intersecting faces", "volume")
    assert diagnose("There are intersecting faces", "surface")


def test_anti_patterns_prefer_the_specific_rule():
    text = "Error: failed to generate the surface mesh\nThere are intersecting faces"
    ids = [d.rule_id for d in diagnose(text, "surface")]
    assert "surface_intersecting_faces" in ids
    assert "surface_mesh_failed" not in ids


def test_repair_ladder_escalates_to_plan_changes():
    report = QualityReport(stage="volume", max_skewness=0.93,
                           min_orthogonal_quality=0.06, verdict=QualityVerdict.POOR)
    assert not is_plan_change(repair_actions(report, 0))
    assert is_plan_change(repair_actions(report, 5))


def test_unusable_mesh_skips_in_place_repair():
    report = QualityReport(stage="volume", max_skewness=0.99,
                           verdict=QualityVerdict.UNUSABLE)
    assert is_plan_change(repair_actions(report, 0))


def test_advisor_is_off_without_credentials_or_flag():
    assert Advisor(AdvisorSettings()).available() is False


def test_advisor_payload_is_whitelisted():
    payload = {
        "diagnosis": "x", "root_cause": "y", "confidence": 0.9, "fatal": False,
        "actions": [
            {"kind": "plan", "operation": "reduce_layers", "description": "azalt",
             "params": [{"name": "count", "value": "2"}]},
            {"kind": "plan", "operation": "os.system", "description": "kötü",
             "params": []},
            {"kind": "shell", "operation": "rm", "description": "kötü", "params": []},
            {"kind": "surface_repair", "operation": "improve_surface_mesh",
             "description": "onar", "params": [{"name": "face_quality_limit",
                                                "value": "0.85"}]},
        ],
    }
    diagnosis = _diagnosis_from_payload(payload, "volume")
    assert [a.operation for a in diagnosis.actions] == [
        "reduce_layers", "improve_surface_mesh"]
    assert diagnosis.actions[0].params["count"] == 2
    assert diagnosis.actions[1].params["face_quality_limit"] == pytest.approx(0.85)
    assert diagnosis.source == "advisor"


def test_advisor_payload_without_valid_actions_is_dropped():
    assert _diagnosis_from_payload(
        {"diagnosis": "", "root_cause": "", "confidence": 0.1, "fatal": False,
         "actions": [{"kind": "plan", "operation": "nope", "description": "",
                      "params": []}]}, "volume") is None


def test_action_validation_rejects_unknown_operations():
    with pytest.raises(ValueError):
        Action(kind=PLAN, operation="drop_database")
    with pytest.raises(ValueError):
        Action(kind=SURFACE_REPAIR, operation="format_disk")
