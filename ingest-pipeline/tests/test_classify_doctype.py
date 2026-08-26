import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

class TestDocTypes(unittest.TestCase):
    def test_doc_types_yaml_exists_and_has_13_types(self):
        ingest = REPO_ROOT / "ingest-pipeline" / "templates" / "classification" / "doc_types.yaml"
        self.assertTrue(ingest.exists(), f"missing ingest {ingest}")
        txt = ingest.read_text(encoding="utf-8")
        tops = re.findall(r"^\s{2}([\w-]+):\s*\n", txt, flags=re.MULTILINE)
        types = [t for t in tops if t not in ("doc_types", "version", "campaign")]
        self.assertEqual(len(types), 14, f"expected 14 doc types, got {len(types)}: {types}")
        for required in ["spec_standard", "anomaly_report", "anomaly_drill_down", "trend_analysis",
                         "onboarding_q_with_answers", "onboarding_q_without_answers", "task_list"]:
            self.assertIn(required, types, f"missing {required}")

if __name__ == "__main__":
    unittest.main()
