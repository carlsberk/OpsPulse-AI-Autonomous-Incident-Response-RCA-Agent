"""Evidence graph: statuses are honest about what was re-verified and what rests on the gate or on inference."""

import uuid

from src.services.evidence_graph import build_evidence_graph
from tests.unit.conftest import API_HEADERS, REPORTER_HEADERS
from tests.unit.factories import incident_payload, make_analysis

TRACE = 'Traceback:\n  File "app.py", line 3, in f\n    return d["name"]\nKeyError: \'name\''
PATCH = '--- a/app.py\n+++ b/app.py\n@@ -3,1 +3,1 @@\n-    return d["name"]\n+    return d.get("name")\n'


def _incident(evidence, evaluation=None, patch=PATCH):
    return {
        "incident_id": "i",
        "error_message": "KeyError: 'name'",
        "stack_trace": TRACE,
        "suggested_patch": patch,
        "analysis": {
            "root_cause_category": "logic_error",
            "affected_files": ["app.py"],
            "evidence": evidence,
            "evaluation": evaluation or {"checks": {"evidence_grounding": {"detail": "ok"}}},
        },
    }


def _item(kind, source, quote, claim="c"):
    return {"kind": kind, "source": source, "quote": quote, "claim": claim}


def _statuses(graph):
    return [n["status"] for n in graph["nodes"] if n["type"] == "evidence"]


def test_statuses_edges_and_anchoring():
    graph = build_evidence_graph(
        _incident(
            [
                _item("observed", "stack_trace", 'return d["name"]'),
                _item("observed", "code_context", 'return d["name"]'),
                _item("inference", "none", ""),
            ]
        )
    )
    assert _statuses(graph) == ["verified_in_record", "verified_by_gate", "not_checked"]
    relations = {(e["from"], e["relation"]) for e in graph["edges"]}
    assert ("evidence-1", "anchors") in relations and ("evidence-3", "suggests") in relations
    assert ("patch", "changes") in relations
    assert graph["flags"] == [] and graph["summary"]["observed_verified"] == 2


def test_unverified_and_missing_evidence_are_flagged():
    detail = "observed evidence #1 quote not found verbatim in code_context: 'x'"
    graph = build_evidence_graph(
        _incident(
            [_item("observed", "code_context", "something else entirely")],
            evaluation={"checks": {"evidence_grounding": {"detail": detail}}},
        )
    )
    assert _statuses(graph) == ["unverified"]
    assert any("no observed evidence was verified" in f for f in graph["flags"])
    only_inference = build_evidence_graph(_incident([_item("inference", "none", "")], patch=""))
    assert any("inference only" in f for f in only_inference["flags"])


def test_stack_trace_quote_that_is_not_in_the_record_is_unverified():
    graph = build_evidence_graph(_incident([_item("observed", "stack_trace", "totally invented line here")]))
    assert _statuses(graph) == ["unverified"]


def test_endpoint(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    resp = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS)
    incident_id = resp.json()["incident_id"]
    graph = client.get(f"/incidents/{incident_id}/evidence-graph", headers=REPORTER_HEADERS)
    assert graph.status_code == 200, graph.text
    body = graph.json()
    assert {n["type"] for n in body["nodes"]} >= {"incident", "claim", "evidence"}
    assert body["summary"]["observed"] >= 1
    assert client.get(f"/incidents/{uuid.uuid4()}/evidence-graph", headers=REPORTER_HEADERS).status_code == 404


def test_quote_is_rechecked_against_the_stored_window_and_hidden_from_the_api(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    resp = client.post("/webhook/incident?wait=true", json=incident_payload(), headers=API_HEADERS)
    body = resp.json()
    assert "source_windows" not in (body.get("analysis") or {})
    incident_id = body["incident_id"]
    assert (
        "source_windows" not in (client.get(f"/incidents/{incident_id}", headers=REPORTER_HEADERS).json()["analysis"])
    )

    graph = client.get(f"/incidents/{incident_id}/evidence-graph", headers=REPORTER_HEADERS).json()
    statuses = {n["status"] for n in graph["nodes"] if n["type"] == "evidence"}
    assert "verified_in_record" in statuses and "unverified" not in statuses


def test_a_quote_missing_from_the_stored_window_falls_back_to_the_gate():
    incident = _incident([_item("observed", "code_context", "def only_in_the_full_file(): pass")])
    incident["analysis"]["source_windows"] = {"code_context": "    10 | x = 1\n    11 | y = 2"}
    graph = build_evidence_graph(incident)
    assert _statuses(graph) == ["verified_by_gate"]  # not "unverified": the gate saw the full text
