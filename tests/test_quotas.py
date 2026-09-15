import unittest

try:
    from usage_note.quotas import quota_groups
except ImportError:
    quota_groups = None


def snapshot(limit_id, primary=None, secondary=None, name=None, at="2026-09-12T12:00:00+00:00"):
    return {"at": at, "rate_limits": {"limit_id": limit_id, "limit_name": name,
                                       "primary": primary, "secondary": secondary}}


class QuotaGroupTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(quota_groups, "Quota groups must distinguish independent limit IDs")

    def test_general_weekly_and_spark_two_windows_have_distinct_groups(self):
        weekly = {"window_minutes": 10080, "used_percent": 26, "resets_at": 1789816690}
        spark_hourly = {"window_minutes": 300, "used_percent": 0, "resets_at": 1789378963}
        spark_weekly = {"window_minutes": 10080, "used_percent": 2, "resets_at": 1789965763}
        groups = quota_groups({"latest_rate_limits_by_id": {
            "codex_bengalfox": snapshot("codex_bengalfox", spark_hourly, spark_weekly,
                                       "GPT-5.3-Codex-Spark", "2026-09-12T12:01:00+00:00"),
            "codex": snapshot("codex", weekly)}})
        self.assertEqual([group["id"] for group in groups], ["codex", "codex_bengalfox"])
        self.assertEqual([group["title"] for group in groups], ["通用额度", "GPT-5.3-Codex-Spark"])
        self.assertEqual(groups[0]["windows"], [{"title": "每周", "window": weekly}])
        self.assertEqual(groups[1]["windows"], [{"title": "5 小时", "window": spark_hourly},
                                                 {"title": "每周", "window": spark_weekly}])
        self.assertEqual(groups[0]["at"], "2026-09-12T12:00:00+00:00")
        self.assertEqual(groups[1]["at"], "2026-09-12T12:01:00+00:00")

    def test_window_labels_use_actual_duration_and_keep_optional_general_short_window(self):
        groups = quota_groups({"latest_rate_limits_by_id": {"codex": snapshot(
            "codex", {"window_minutes": 300, "used_percent": 10},
            {"window_minutes": 10080, "used_percent": 20})}})
        self.assertEqual([window["title"] for window in groups[0]["windows"]], ["5 小时", "每周"])
        odd = quota_groups({"latest_rate_limits_by_id": {"codex": snapshot(
            "codex", {"window_minutes": 90, "used_percent": 1})}})
        self.assertEqual(odd[0]["windows"][0]["title"], "90 分钟")

    def test_unknown_ids_and_missing_ids_are_never_general_or_spark(self):
        groups = quota_groups({"latest_rate_limits_by_id": {
            "premium": snapshot("premium"),
            "future-quota": snapshot("future-quota", {"window_minutes": 10080}, name="General"),
            "unclassified": snapshot(None, {"window_minutes": 300}, name="GPT-5.3-Codex-Spark")}})
        self.assertEqual({group["id"] for group in groups}, {"premium", "future-quota", "unclassified"})
        for group in groups:
            self.assertNotIn(group["title"], {"通用额度", "GPT-5.3-Codex-Spark"})
        self.assertEqual(next(group for group in groups if group["id"] == "premium")["windows"], [])
        self.assertEqual(next(group for group in groups if group["id"] == "unclassified")["title"], "未分类额度")

    def test_missing_percentage_is_preserved_and_null_windows_are_not_invented(self):
        window = {"used_percent": None, "window_minutes": 300}
        groups = quota_groups({"latest_rate_limits_by_id": {"codex": snapshot("codex", window)}})
        self.assertEqual(groups[0]["windows"], [{"title": "5 小时", "window": window}])
        self.assertIsNone(groups[0]["windows"][0]["window"]["used_percent"])
        empty = quota_groups({"latest_rate_limits_by_id": {"codex": snapshot("codex")}})
        self.assertEqual(empty[0]["windows"], [])

    def test_legacy_snapshot_is_classified_by_its_id_only(self):
        rate = {"limit_id": "codex_bengalfox", "primary": {"window_minutes": 300, "used_percent": 2}}
        groups = quota_groups({"latest_rate_limits": rate, "latest_rate_limits_at": "2026-09-12T12:00:00+00:00"})
        self.assertEqual(groups[0]["id"], "codex_bengalfox")
        self.assertEqual(groups[0]["title"], "GPT-5.3-Codex-Spark")
        self.assertEqual(quota_groups({}), [])


if __name__ == "__main__":
    unittest.main()
