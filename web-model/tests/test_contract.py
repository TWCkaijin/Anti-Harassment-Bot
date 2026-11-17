import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CompletionDataset, parse_output
from evaluate import extract, score
from prepare_data import build_cases, expand, validate


class ContractTests(unittest.TestCase):
    def test_splits_and_offsets(self):
        cases = build_cases()
        self.assertEqual(sum(v["cases"] for v in validate(cases).values()), 360)
        invalid = copy.deepcopy(cases)
        invalid[0]["entities"][0]["start"] += 1
        with self.assertRaises(AssertionError):
            validate(invalid)

    def test_family_leak_is_rejected(self):
        cases = build_cases()
        cases[-1]["family"] = cases[0]["family"]
        with self.assertRaises(AssertionError):
            validate(cases)

    def test_invalid_round_discards_all_entities(self):
        answers = iter(['{"spans":["甲","乙"]}', '{"type":"person"}', '{"type":"invented"}'])
        output = extract("甲和乙", lambda *_: next(answers))
        self.assertFalse(output["valid"])
        self.assertEqual(output["entities"], [])
        self.assertEqual(len(output["rounds"]), 3)

    def test_hallucination_rejected_and_duplicates_collapsed(self):
        with self.assertRaises(ValueError):
            parse_output('{"spans":["不存在"]}', "原文")
        self.assertEqual(parse_output('{"spans":["原文","原文"]}', "原文"), ["原文"])

    def test_metrics_invalid_cases_are_false_negatives(self):
        cases = [
            {"id": "1", "text": "甲", "entities": [{"text": "甲", "type": "person"}]},
            {"id": "2", "text": "無", "entities": []},
        ]
        result = score(
            cases,
            [
                {"id": "1", "valid": False, "entities": []},
                {"id": "2", "valid": True, "entities": []},
            ],
        )
        self.assertEqual(result["micro"]["fn"], 1)
        self.assertEqual(result["micro"]["recall"], 0)
        self.assertEqual(result["case_exact_rate"], 0.5)
        with self.assertRaises(ValueError):
            score(cases, [{"id": "1", "valid": False}])

    def test_answer_only_mask_and_no_silent_truncation(self):
        class Tokenizer:
            eos_token_id = 0

            def apply_chat_template(self, messages, **kwargs):
                assert kwargs["enable_thinking"] is False
                return "PROMPT\n"

            def encode(self, value, **kwargs):
                return list(value.encode())

        rows = expand(build_cases()[:1])[:1]
        ds = CompletionDataset(rows, Tokenizer(), 500)
        tokens, offset = ds[0]
        self.assertEqual(offset, len("PROMPT\n"))
        self.assertEqual(tokens[-1], 0)
        with self.assertRaises(ValueError):
            CompletionDataset(rows, Tokenizer(), 1)


if __name__ == "__main__":
    unittest.main()
