from app.modules.pretests.diagnosis_service import _recommended_path


def test_confirmed_evidence_directed_gap_prioritizes_prerequisite_repair() -> None:
    nodes = [
        {
            "concept_code": "target",
            "role": "target",
            "depth": 0,
            "status": "fragile",
        },
        {
            "concept_code": "identified.skill",
            "role": "prerequisite",
            "depth": 2,
            "status": "probably_gap",
        },
    ]

    assert _recommended_path(
        nodes=nodes,
        stop_reason="evidence_directed_gap_confirmed",
    ) == "repair_prerequisites"
