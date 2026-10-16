"""Regression guards for owner-controlled membership approval.
These source-level checks intentionally use stdlib only; full HTTP integration
requires an isolated runtime and test user accounts.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManualApprovalRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = (ROOT / "app.py").read_text(encoding="utf-8")
        cls.cloud = (ROOT / "cloud_app.py").read_text(encoding="utf-8")

    def test_new_members_are_pending_unless_owner_enables_automatic_approval(self):
        self.assertIn("'approved' if s['autoApprove'] else 'pending'", self.core)
        self.assertNotIn("'approved' if (TEMP_OPEN_ACCESS or s['autoApprove'])", self.core)

    def test_server_restart_must_not_auto_approve_pending_members(self):
        self.assertNotIn("UPDATE members SET state='approved'", self.core)
        self.assertNotIn("if TEMP_OPEN_ACCESS:", self.core)

    def test_one_time_migration_persists_manual_approval(self):
        self.assertIn("manual-approval-20261017.v1", self.cloud)
        self.assertIn("current['autoApprove']=False", self.cloud)
        self.assertNotIn("UPDATE members SET state=", self.cloud)

    def test_admin_must_approve_and_no_sms_required_by_default(self):
        self.assertIn("'approve':({'pending'},'approved')", self.core)
        self.assertIn("os.getenv('REQUIRE_PHONE_VERIFICATION','false')", self.core)


if __name__ == "__main__":
    unittest.main()
