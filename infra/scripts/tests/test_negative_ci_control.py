import unittest


class NegativeCiControlTests(unittest.TestCase):
    def test_infra_gate_reports_a_controlled_failure(self):
        self.fail("CONTROLLED_NEGATIVE_INFRA_CI_PROOF")
