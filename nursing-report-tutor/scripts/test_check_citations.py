import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from check_citations import Net, check, main, parse_entries, report, verify_references, verify_report

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

    def test_chinese_narrative_with_prefix(self):
        # 「國內黃等（2024）」抓到的是「國內黃」，要對得上參考資料的「黃靖恩」
        r = check("國內黃等（2024）指出…\n參考文獻\n黃靖恩、蘇維仁（2024）．篇名．長庚護理，35(2)，1–15。\n")
        self.assertEqual(r["missing_in_refs"], [])
        self.assertEqual(r["never_cited"], [])


# ---------------------------------------------------------------- 查文獻：全部用假回應，不打真的網路

WYATT = ("Wyatt, P. B., Nelson, C. T., & Patel, N. K. (2023). The role of cryotherapy after total knee arthroplasty: "
         "A systematic review. The Journal of Arthroplasty, 38(5), 950–956. https://doi.org/10.1016/j.arth.2022.12.004")
WYATT_CSL = {"title": "The Role of Cryotherapy After Total Knee Arthroplasty: A Systematic Review",
             "author": [{"family": "Wyatt", "given": "Phillip B."}], "issued": {"date-parts": [[2023, 5]]},
             "published-online": {"date-parts": [[2022, 12, 8]]},
             "container-title": "The Journal of Arthroplasty", "DOI": "10.1016/j.arth.2022.12.004"}
HUANG = "黃靖恩、蘇維仁（2024）．冰敷處置於人工全膝關節置換術後成效探討．長庚護理，35(2)，1–15。https://doi.org/10.6386/CGN.202406_35(2).0001"
HUANG_CSL = {"title": "冰敷處置於人工全膝關節置換術後成效探討", "author": [{"literal": "黃靖恩"}],
             "issued": {"date-parts": [["2024", "6", "1"]]}, "container-title": "長庚護理"}
PUBMED_123 = {"result": {"uids": ["123"], "123": {
    "uid": "123", "title": "Ice for knees.", "authors": [{"name": "Example A"}], "pubdate": "2021 Mar",
    "fulljournalname": "Journal of Examples", "source": "J Ex", "articleids": [{"idtype": "doi", "value": "10.1/ex"}]}}}


class FakeFetch:
    """假網路：網址包含 key 就回對應的 (狀態碼, JSON)；沒對到回 404。offline=True 就像沒網路一樣丟 OSError。"""

    def __init__(self, routes=None, offline=False):
        self.routes, self.offline, self.calls = routes or {}, offline, []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.offline:
            raise OSError("network unreachable")
        for key, (status, data) in self.routes.items():
            if key in url:
                return status, json.dumps(data).encode("utf-8")
        return 404, b""


def fake_net(**kw):
    fetch = FakeFetch(**kw)
    return Net(fetch=fetch, sleep=lambda s: None), fetch


def verify(lines, **kw):
    net, fetch = fake_net(**kw)
    v = verify_references(lines, net)
    return v, verify_report(v), fetch


class VerifyReferencesTest(unittest.TestCase):
    def test_found_and_matching(self):
        v, text, _ = verify([WYATT, HUANG], routes={"arth.2022": (200, WYATT_CSL), "CGN.2024": (200, HUANG_CSL)})
        self.assertEqual([r["status"] for r in v["results"]], ["ok", "ok"])
        self.assertIn("【相符】Wyatt, 2023", text)
        self.assertIn("【相符】黃靖恩, 2024", text)  # 中文作者對中文；online／print 年份任一個對上都算

    def test_mismatch_lists_differences(self):
        wrong = WYATT.replace("(2023)", "(2020)").replace("The Journal of Arthroplasty", "Knee")
        v, text, _ = verify([wrong], routes={"arth.2022": (200, WYATT_CSL)})
        self.assertEqual(v["results"][0]["status"], "mismatch")
        self.assertIn("年份不同（你寫 2020，資料庫是 2022／2023）", text)
        self.assertIn("期刊名稱不同", text)
        self.assertNotIn("篇名跟資料庫不同", text)

    def test_doi_points_to_another_paper(self):
        other = dict(WYATT_CSL, title="Something completely unrelated about diabetes",
                     author=[{"family": "Other"}], issued={"date-parts": [[2010]]})
        other.pop("published-online")
        v, text, _ = verify([WYATT], routes={"arth.2022": (200, other)})
        self.assertEqual(v["results"][0]["status"], "mismatch")
        self.assertIn("查到的是另一篇文章", text)

    def test_doi_not_found(self):
        v, text, _ = verify([WYATT.replace("arth.2022", "arth.2099")], routes={})
        self.assertEqual(v["results"][0]["status"], "notfound")
        self.assertIn("【找不到】", text)
        self.assertIn("https://doi.org/10.1016/j.arth.2099.12.004", text)

    def test_doi_typo_found_by_title(self):
        item = dict(WYATT_CSL, title=[WYATT_CSL["title"]], **{"container-title": [WYATT_CSL["container-title"]]})
        v, text, _ = verify([WYATT.replace("arth.2022", "arth.2099")],
                            routes={"api.crossref.org/works?": (200, {"message": {"items": [item]}})})
        self.assertEqual(v["results"][0]["status"], "mismatch")
        self.assertIn("用篇名找到一篇，它的 DOI 是 10.1016/j.arth.2022.12.004", text)

    def test_pmid_lookup(self):
        line = "Example, A. (2021). Ice for knees. Journal of Examples, 1(1), 1-2. PMID: 123"
        v, text, fetch = verify([line], routes={"esummary.fcgi": (200, PUBMED_123)})
        self.assertEqual(v["results"][0]["status"], "ok")
        self.assertIn("【相符】Example, 2021", text)
        self.assertIn("tool=nursing-report-tutor", fetch.calls[0][0])

    def test_no_doi_english_title_search_not_found(self):
        line = "Smith, J. (2023). A fabricated trial of ice. Journal of Nothing, 1(1), 1-9."
        v, text, _ = verify([line], routes={"api.crossref.org/works?": (200, {"message": {"items": []}})})
        self.assertEqual(v["results"][0]["status"], "notfound")

    def test_skips_web_pages_and_chinese_without_doi(self):
        lines = ["Critical Appraisal Skills Programme. (2024a). CASP checklist. https://casp-uk.net/x.pdf",
                 "陳○○（2023）。虛構篇名。虛構期刊，5(1)，41-50。"]
        v, text, fetch = verify(lines)
        self.assertEqual([r["status"] for r in v["results"]], ["skipped", "skipped"])
        self.assertEqual(fetch.calls, [])  # 沒要查就不連網
        self.assertIn("Critical Appraisal Skills Programme, 2024a", text)

    def test_offline_does_not_fail(self):
        v, text, fetch = verify([WYATT, HUANG], offline=True)
        self.assertTrue(v["offline"])
        self.assertEqual(len(fetch.calls), 1)  # 第一次就連不上，後面不再試
        self.assertIn("無法連網查證", text)

    def test_proxy_block_counts_as_offline(self):
        v, text, _ = verify([WYATT], routes={"doi.org": (403, {})})
        self.assertTrue(v["offline"])

    def test_main_offline_still_cross_checks(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("內文（Wyatt et al., 2023; Trial, 2022）。\n參考文獻\n" + WYATT + "\n")
            net, _ = fake_net(offline=True)
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["x", path], net=net)
        self.assertEqual(code, 1)  # 對引用的問題照樣抓到
        self.assertIn("Trial, 2022", out.getvalue())
        self.assertIn("無法連網查證", out.getvalue())

    def test_main_offline_flag_never_touches_network(self):
        net, fetch = fake_net()
        with redirect_stdout(io.StringIO()):
            main(["x", os.path.join(HERE, "範例-對引用.txt"), "--offline"], net=net)
        self.assertEqual(fetch.calls, [])

    def test_polite_and_rate_limited(self):
        waits = []
        clock = iter([0.0, 0.1, 0.1, 0.2]).__next__
        fetch = FakeFetch(routes={"arth.2022": (200, WYATT_CSL), "CGN.2024": (200, HUANG_CSL)})
        net = Net(fetch=fetch, sleep=waits.append, clock=clock)
        verify_references([WYATT, HUANG], net)
        ua = fetch.calls[0][1]["User-Agent"]
        self.assertNotIn("@", ua)  # 不帶任何 email
        self.assertLessEqual(fetch.calls[0][2], 10)  # 逾時短
        self.assertTrue(waits and waits[0] > 0)  # 兩次查詢之間有等

    def test_same_answer_every_time_online(self):
        routes = {"arth.2022": (200, WYATT_CSL), "CGN.2024": (200, HUANG_CSL)}
        self.assertEqual(verify([WYATT, HUANG], routes=routes)[1], verify([WYATT, HUANG], routes=routes)[1])

    def test_parse_doi_with_parentheses(self):
        e = parse_entries([HUANG])[0]
        self.assertEqual(e["doi"], "10.6386/CGN.202406_35(2).0001")

if __name__ == "__main__":
    unittest.main()
