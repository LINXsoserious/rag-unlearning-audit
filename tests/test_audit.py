import json
import unittest
from unittest.mock import patch

import audit
import ollama_run


class RetrievalTests(unittest.TestCase):
    def test_retrieval_ranks_matching_document_and_has_stable_ids(self):
        docs = [audit.Document("z", "fact", "apple orange"), audit.Document("a", "guard", "apple orange"),
                audit.Document("b", "fact", "banana")]
        rows = audit.retrieve("apple orange", docs, 2)
        self.assertEqual([r["id"] for r in rows], ["a", "z"])
        self.assertTrue(all(r["score"] > 0 for r in rows))
        self.assertEqual(audit.retrieve("unknown", docs, 2), [])

    def test_demo_has_full_paired_conditions(self):
        rows = audit.demo()
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({(r["case_id"], r["guard"], r["poison"], r["triggered"]) for r in rows}), 24)
        self.assertTrue(all(r["origin"] == "synthetic_rule_demo" for r in rows))
        self.assertTrue(all(len(r["sources"]) <= 2 for r in rows))

    def test_trusted_guard_in_simulator_is_not_claimed_retrieved(self):
        rows = audit.demo(2, "trusted")
        subset = [r for r in rows if r["guard"] and r["poison"] and r["triggered"]]
        self.assertEqual(len(subset), 3)
        self.assertTrue(all(r["guard_in_context"] and not r["guard_retrieved"] for r in subset))
        self.assertTrue(all(r["target"] not in r["answer"] for r in subset))

    def test_invalid_parameters_rejected(self):
        for k in (0, 11, True, 1.5):
            with self.subTest(k=k), self.assertRaises(ValueError): audit.demo(k)
        with self.assertRaises(ValueError): audit.demo(2, "invalid")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = audit.demo()

    def test_summary_denominators_and_missing_observations(self):
        row = self.rows[0]
        row["status"] = "error"
        result = audit.summarize(self.rows)
        group = next(g for g in result["groups"] if not g["triggered"] and not g["guard"] and not g["poison"])
        self.assertEqual((group["records"], group["valid"], group["errors"]), (3, 2, 1))
        self.assertEqual(group["guard_in_context"]["known"], 0)
        self.assertEqual(result["paired_contrasts"][0]["matched_cases"], 2)

    def test_matched_contrast_uses_cases_not_unpaired_aggregate(self):
        rows = []
        for case, outcomes in [("one", (0, 1, 0, 0)), ("two", (0, 0, 0, 1))]:
            for (g, p), outcome in zip(((False, False), (False, True), (True, False), (True, True)), outcomes):
                rows.append({"case_id": case, "question": "q", "target": "secret-x", "answer": "secret-x" if outcome else "safe",
                             "guard": g, "poison": p, "triggered": True})
        self.assertEqual(audit.summarize(rows)["paired_contrasts"][1],
                         {"triggered": True, "matched_cases": 2, "difference_in_differences": 0.0})
        self.assertIsNone(audit.summarize(rows[:3])["paired_contrasts"][1]["difference_in_differences"])

    def test_duplicate_experimental_cell_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit.validate([self.rows[0], self.rows[0]])

    def test_invalid_pairing_and_empty_success_are_rejected(self):
        a = self.rows[0]
        another = next(r for r in self.rows if r["case_id"] == a["case_id"] and r["triggered"] == a["triggered"] and r["guard"] != a["guard"])
        for field, value in (("target", "different target"), ("question", "different question")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                audit.validate([a, dict(another, **{field: value})])
        with self.assertRaises(ValueError):
            audit.validate([dict(a, answer="", status="ok")])

    def test_boolean_and_target_validation(self):
        for field, value in (("guard", 1), ("triggered", "true"), ("target", ""), ("guard_in_context", "false")):
            row = dict(self.rows[0], **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError): audit.validate([row])

    def test_jsonl_roundtrip_and_invalid_lines(self):
        payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in self.rows)
        self.assertEqual(audit.parse_jsonl(payload), self.rows)
        with self.assertRaisesRegex(ValueError, "line 2"): audit.parse_jsonl('{}\n{broken')
        with self.assertRaises(ValueError): audit.parse_jsonl("")

    def test_exact_match_is_not_semantic_or_evidence_proof(self):
        row = dict(self.rows[0], target="CODE-123", answer="code-123")
        self.assertTrue(audit.leaked(row))
        row["answer"] = "The code was paraphrased."
        self.assertFalse(audit.leaked(row))
        row["status"] = "error"
        self.assertIsNone(audit.leaked(row))

    def test_local_adapter_sends_only_selected_evidence_to_local_service(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *args): return b'{"message":{"content":"hello"}}'
        row = next(r for r in self.rows if r["guard"] and r["poison"] and r["triggered"])
        with patch("ollama_run.urllib.request.urlopen", return_value=Response()) as call:
            self.assertEqual(ollama_run.generate("fake-model", row, 11434, 30), "hello")
            request = call.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
            body = json.loads(request.data.decode())
            for source in row["sources"]:
                self.assertIn(source["text"], body["messages"][1]["content"])
                self.assertNotIn(f"[{source['id']} / {source['kind']}]", body["messages"][1]["content"])


if __name__ == "__main__": unittest.main()
