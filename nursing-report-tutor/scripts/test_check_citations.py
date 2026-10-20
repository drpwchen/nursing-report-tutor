import os
import unittest

from check_citations import check, report

HERE = os.path.dirname(os.path.abspath(__file__))


class CheckCitationsTest(unittest.TestCase):
    def test_sample_file(self):
        with open(os.path.join(HERE, "範例-對引用.txt"), encoding="utf-8") as f:
            r = check(f.read())
        self.assertEqual(r["missing_in_refs"], [("Trial", "2022")])
        self.assertEqual(r["never_cited"], [("Trial", "2020"), ("Unused", "2018")])
        self.assertIn(("陳", "2023"), r["cited"] & r["listed"])

    def test_all_matched(self):
        text = "內文（Example, 2021; Sample, 2020）。\n參考資料\nExample, A. (2021). x.\nSample, B. (2020). y.\n"
        r = check(text)
        self.assertEqual(r["missing_in_refs"], [])
        self.assertEqual(r["never_cited"], [])
        self.assertIn("全部對上了", report(r))

    def test_no_reference_heading(self):
        self.assertIsNone(check("只有內文（Example, 2021）。"))
        self.assertIn("找不到", report(None))

    def test_hospital_report_format(self):
        # 院內通過的報告：標題「伍、參考文獻」、中文引用寫「王小明等」或只寫姓、外文名有重音字母
        text = (
            "壹、臨床現況分析及動機\n"
            "疼痛困擾（王小明等，2016）；評估（林等，2018）；另見（Pérez & Sample, 2020；Example et al., 2019）。\n"
            "伍、參考文獻\n"
            "王小明、李大華、張美玲(2016)．虛構篇名．虛構期刊，1(1)，1-10。\n"
            "林美惠、陳志明（2018）．虛構篇名．虛構期刊，2(1)，11-20。\n"
            "林美惠（2019）．虛構篇名．虛構期刊，3(1)，21-30。\n"
            "Pérez, I., & Sample, B. (2020). x.\n"
            "Example, A., Sample, B., & Demo, C. (2019). y.\n"
        )
        r = check(text)
        self.assertEqual(r["missing_in_refs"], [])
        self.assertEqual(r["never_cited"], [("林美惠", "2019")])

    def test_narrative_two_authors(self):
        r = check("Example & Sample (2020) 指出…\n參考資料\nExample, A., & Sample, B. (2020). x.\n")
        self.assertEqual(r["missing_in_refs"], [])
        self.assertEqual(r["never_cited"], [])

    def test_reading_report_example_matches(self):
        # 範例-讀書報告.md 是照評分表寫的示範，引用必須全部對上
        with open(os.path.join(HERE, "..", "範例-讀書報告.md"), encoding="utf-8") as f:
            r = check(f.read())
        self.assertEqual(r["missing_in_refs"], [])
        self.assertEqual(r["never_cited"], [])
        self.assertEqual(len(r["listed"]), 3)

    def test_same_answer_every_time(self):
        text = "Example (2021) 說…\n## 參考資料\nExample, A. (2020). x.\n"
        self.assertEqual(report(check(text)), report(check(text)))
        self.assertEqual(check(text)["missing_in_refs"], [("Example", "2021")])


if __name__ == "__main__":
    unittest.main()
