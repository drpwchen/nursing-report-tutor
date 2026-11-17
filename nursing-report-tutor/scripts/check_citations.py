"""對引用＋查文獻：檢查報告的引用，分兩步。

這是一支程式，不是 AI：同一份報告丟進來，每次答案都一樣（查網路那一步，資料庫沒改就一樣）。

第一步「對引用」（不用網路）：內文引用和參考資料有沒有對上，只對「作者姓氏＋年份」。
第二步「查文獻」（要網路）：每一筆參考資料，用 DOI、PMID 或篇名去查
  doi.org（DOI 登記機構，含 CrossRef、華藝）、CrossRef、PubMed，回報三種結果：
  找到且相符／找到但書目不符（列出標題、第一作者、年份、期刊哪裡不同）／找不到。
  沒有 DOI 的網頁資料、沒有 DOI 的中文文獻，程式不查，會請你自己查。
  連不上網時不會失敗：第一步照做，並提醒你改用瀏覽器逐篇查。

程式不判斷的事：內文寫的結論有沒有被原文支持、文獻寫得好不好——要讀摘要或原文判斷。

用法：
    python check_citations.py 報告.txt              # 對引用＋查文獻
    python check_citations.py 報告.txt --offline    # 只對引用，不連網
    python check_citations.py 報告.txt --abstracts  # 另外印出找到的文獻摘要（給 AI 判斷內文有沒有引錯）

報告裡要有一行標題「參考資料」（或「參考文獻」「伍、參考文獻」、References），上面是內文、下面是參考資料，一行一筆。
內文引用支援 APA 常見寫法：(Example, 2021)、(Example & Sample, 2020)、(Example et al., 2019)、
(Example, 2021; Sample, 2020)、Example (2021)、（陳, 2022）、（王小明等，2016）。
中文只寫姓（例：（王等，2016）、國內黃等（2024））時，對到同年、同姓開頭的參考資料就算對上。
"""

import html
import http.client
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

REF_HEADINGS = ("參考資料", "參考文獻", "References", "REFERENCES", "Reference")

# 標題前面的章節編號（例：「伍、」「五、」「5.」）和後面的括號註記（例：「(5%)」「〔七、5 分〕」）
HEADING_NUMBER = re.compile(r"^(?:[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]+[、.．]|\d+[、.．])\s*")
HEADING_NOTE = re.compile(r"\s*[（(〔\[][^（）()〔〕\[\]]*[）)〕\]]$")
# 外文姓氏（含 é、ñ 這類字母）或 1–4 個中文字
NAME = r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+|[一-鿿]{1,4}"

# 括號裡的一段引用，例：Example et al., 2019（全形或半形括號都可以）
PAREN = re.compile(r"[（(]([^（）()]*?\d{4}[a-z]?)[）)]")
# 敘述式引用，例：Example (2021)、Example et al. (2019)、Example & Sample (2020)、陳 (2022)
NARRATIVE = re.compile(
    r"(" + NAME + r")(?:\s*(?:&|and|與|、)\s*(?:" + NAME + r"))?(?: et al\.| 等)?\s*[（(](\d{4}[a-z]?)[）)]"
)
ONE = re.compile(r"(" + NAME + r")[^;；,，]*?[,，]\s*(\d{4}[a-z]?)")
# 參考資料一筆的開頭：第一作者姓氏 … (年份)
REF = re.compile(r"^\s*(" + NAME + r")[^()（）]*?[（(](\d{4}[a-z]?)[）)]")

DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>，。；]+")
PMID = re.compile(r"PMID[:：]?\s*(\d{1,9})", re.I)
URL = re.compile(r"https?://\S+")
CJK = re.compile(r"[一-鿿]")


def is_cjk(s):
    return bool(s) and "一" <= s[0] <= "鿿"


def clean_name(name):
    """中文名字後面的「等」「等人」不算名字。"""
    for tail in ("等人", "等"):
        if len(name) > len(tail) and name.endswith(tail) and is_cjk(name):
            return name[: -len(tail)]
    return name


def same_source(cited, listed):
    """作者、年份都一樣才算對上；中文只寫姓時，同姓開頭就算。
    中文敘述式引用前面常黏著別的字（「國內黃等（2024）」抓到「國內黃」），所以取它的結尾去對參考資料的開頭。"""
    (a, ya), (b, yb) = cited, listed
    if ya != yb:
        return False
    if a == b:
        return True
    return is_cjk(a) and any(b.startswith(a[k:]) for k in range(len(a)))


def heading_text(line):
    """去掉 Markdown 符號、章節編號、括號註記，剩下標題本身。"""
    text = HEADING_NUMBER.sub("", line.strip().strip("#*：: "))
    return HEADING_NOTE.sub("", text).strip("#*：: ")


def split_report(text):
    """以「參考資料」標題那一行切成內文與參考資料兩段。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if heading_text(line) in REF_HEADINGS:
            return "\n".join(lines[:i]), lines[i + 1:]
    return text, []


def in_text_citations(body):
    found = set()
    for m in PAREN.finditer(body):
        for part in re.split(r"[;；]", m.group(1)):
            one = ONE.search(part)
            if one:
                found.add((clean_name(one.group(1)), one.group(2)))
    for m in NARRATIVE.finditer(body):
        found.add((clean_name(m.group(1)), m.group(2)))
    return found


def strip_bullet(line):
    return line.lstrip("*-•·> \t")  # 粗體、清單符號不算


def reference_entries(ref_lines):
    found = set()
    for line in ref_lines:
        m = REF.match(strip_bullet(line))
        if m:
            found.add((m.group(1), m.group(2)))
    return found


def check(text):
    body, ref_lines = split_report(text)
    if not ref_lines:
        return None
    cited = in_text_citations(body)
    listed = reference_entries(ref_lines)
    return {
        "cited": cited,
        "listed": listed,
        "ref_lines": ref_lines,
        "missing_in_refs": sorted(c for c in cited if not any(same_source(c, l) for l in listed)),
        "never_cited": sorted(l for l in listed if not any(same_source(c, l) for c in cited)),
    }


def report(result):
    if result is None:
        return "找不到「參考資料」這一行標題，沒辦法對。請在參考資料清單上面加一行「參考資料」。"
    fmt = lambda pairs: "\n".join(f"  - {a}, {y}" for a, y in pairs) or "  （沒有）"
    lines = [
        f"內文引用 {len(result['cited'])} 筆，參考資料 {len(result['listed'])} 筆。",
        "內文有引用、參考資料裡找不到：",
        fmt(result["missing_in_refs"]),
        "參考資料有列、內文沒有引用：",
        fmt(result["never_cited"]),
    ]
    if not result["missing_in_refs"] and not result["never_cited"]:
        lines.append("全部對上了。（這一步只對作者和年份。）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 第二步：查文獻（要網路）

USER_AGENT = "nursing-report-tutor/1.0 (citation checker; python-urllib)"
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
NCBI_TOOL = "nursing-report-tutor"
OFFLINE_MESSAGE = (
    "無法連網查證：這個環境連不到外部網站，所以沒有查文獻存不存在。\n"
    "上面「對引用」的結果照樣有效。每一篇文獻是不是真的，請讓 AI 用瀏覽器／搜尋逐篇查，\n"
    "或自己把 DOI 貼到瀏覽器（https://doi.org/ 後面接 DOI）看打不打得開、篇名作者對不對。"
)


def _urlopen(url, headers, timeout):
    """真的連網。回 (HTTP 狀態碼, 內容)；完全連不上時丟 OSError。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


class Net:
    """查詢都經過這裡：逾時短、兩次查詢至少間隔 min_interval 秒、同一網址只查一次。
    第一次查詢就連不上（或被代理擋 403/407）→ 判定沒網路，後面全部不查。"""

    def __init__(self, fetch=_urlopen, timeout=8, min_interval=0.4, sleep=time.sleep, clock=time.monotonic):
        self.fetch, self.timeout, self.min_interval = fetch, timeout, min_interval
        self.sleep, self.clock = sleep, clock
        self.offline = False
        self.reached = False  # 有沒有成功連上過任何一次
        self._last = None
        self._cache = {}

    def _once(self, url, accept):
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        try:
            return self.fetch(url, headers, self.timeout)
        except (OSError, ValueError, http.client.HTTPException):  # 連不上、逾時、被中斷
            return None, b""

    def get(self, url, accept="application/json"):
        """回 ("ok", bytes)／("notfound", None)／("error", None)／("offline", None)。"""
        if self.offline:
            return "offline", None
        if url in self._cache:
            return self._cache[url]
        status, body = self._once(url, accept)
        if status in (429, 500, 502, 503, 504):  # 伺服器忙：等一下再試一次
            self.sleep(1.5)
            status, body = self._once(url, accept)
        if status is None or (status in (403, 407) and not self.reached):
            if not self.reached:
                self.offline = True
                return "offline", None
            out = ("error", None)
        elif status == 200:
            self.reached = True
            out = ("ok", body)
        elif status in (404, 410):
            self.reached = True
            out = ("notfound", None)
        else:
            out = ("error", None)
        self._cache[url] = out
        return out

    def get_json(self, url, accept="application/json"):
        state, body = self.get(url, accept)
        if state != "ok":
            return state, None
        try:
            return "ok", json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return "error", None


def norm(s):
    """比對用：去掉 HTML 標籤、重音、標點、空白，全部小寫；「&」當成 and，開頭的 The 不算。"""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", "and")
    s = re.sub(r"^\s*the\s+", "", s)
    return "".join(c for c in s if c.isalnum())


def title_coverage(title, line):
    """資料庫的篇名有幾成出現在這一筆參考資料裡（只算連續 4 個字以上的片段，零星字母不算）。"""
    t, l = norm(title), norm(line)
    if not t:
        return 0.0
    if t in l:
        return 1.0
    if len(t) < 8:
        return 0.0
    blocks = SequenceMatcher(None, t, l, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 4) / len(t)


def _years(*dates):
    out = set()
    for d in dates:
        if isinstance(d, dict):
            for parts in d.get("date-parts") or []:
                if parts and parts[0]:
                    out.add(str(parts[0])[:4])
        elif isinstance(d, str):
            m = re.match(r"\s*(\d{4})", d)
            if m:
                out.add(m.group(1))
    return out


def _first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def from_csl(d, doi=None):
    """doi.org 回的 CSL JSON 或 CrossRef API 的一筆 → 統一格式。"""
    authors = d.get("author") or []
    first = authors[0] if authors else {}
    journals = []
    for field in ("container-title", "short-container-title"):
        v = d.get(field) or []
        journals += [v] if isinstance(v, str) else [j for j in v if j]
    return {
        "title": html.unescape(re.sub(r"<[^>]+>", "", _first(d.get("title")))).strip(),
        "author": first.get("family") or first.get("literal") or first.get("name") or "",
        "years": _years(d.get("issued"), d.get("published-print"), d.get("published-online")),
        "journals": journals,
        "doi": d.get("DOI") or doi or "",
        "pmid": "",
        "abstract": d.get("abstract") or "",
    }


def from_pubmed(s, pmid):
    authors = s.get("authors") or []
    name = authors[0].get("name", "") if authors else ""
    doi = next((a.get("value", "") for a in s.get("articleids") or [] if a.get("idtype") == "doi"), "")
    return {
        "title": html.unescape(s.get("title", "")).strip(),
        "author": name.rsplit(" ", 1)[0] if " " in name else name,  # 「Wyatt PB」→ Wyatt
        "years": _years(s.get("pubdate"), s.get("epubdate")),
        "journals": [j for j in (s.get("fulljournalname"), s.get("source")) if j],
        "doi": doi,
        "pmid": pmid,
        "abstract": "",
    }


def lookup_doi(net, doi):
    url = "https://doi.org/" + urllib.parse.quote(doi, safe="/():;")
    state, data = net.get_json(url, accept="application/vnd.citationstyles.csl+json")
    return state, (from_csl(data, doi) if state == "ok" else None)


def lookup_pmid(net, pmid):
    url = f"{NCBI}esummary.fcgi?db=pubmed&retmode=json&tool={NCBI_TOOL}&id={pmid}"
    state, data = net.get_json(url)
    if state != "ok":
        return state, None
    s = (data.get("result") or {}).get(pmid)
    if not s or s.get("error"):
        return "notfound", None
    return "ok", from_pubmed(s, pmid)


def search_title(net, line):
    """沒有 DOI 的英文文獻（或 DOI 查不到時）：把整筆參考資料丟給 CrossRef 搜，取篇名最像的一篇。"""
    query = URL.sub(" ", line)[:300]
    url = ("https://api.crossref.org/works?rows=5&query.bibliographic=" + urllib.parse.quote(query)
           + "&select=DOI,title,author,issued,published-print,published-online,container-title,short-container-title")
    state, data = net.get_json(url)
    if state != "ok":
        return state, None
    best, best_cov = None, 0.0
    for item in (data.get("message") or {}).get("items") or []:
        meta = from_csl(item)
        cov = title_coverage(meta["title"], line)
        if cov > best_cov:
            best, best_cov = meta, cov
    if best is None or best_cov < 0.9:
        return "notfound", None
    return "ok", best


def compare(entry, meta):
    """逐欄比對，回傳 [(欄位, 白話說明)]。中文名對英文拼音這種比不了的就不比。"""
    diffs = []
    line = entry["line"]
    if meta["title"] and title_coverage(meta["title"], line) < 0.9:
        diffs.append(("title", f"篇名跟資料庫不同，資料庫是「{meta['title']}」"))
    a, b = norm(entry["author"] or ""), norm(meta["author"])
    if a and b and is_cjk(a) == is_cjk(b):
        ok = a == b or (is_cjk(a) and (a.startswith(b) or b.startswith(a)))
        if not ok:
            diffs.append(("author", f"第一作者不同（你寫 {entry['author']}，資料庫是 {meta['author']}）"))
    year = (entry["year"] or "")[:4]
    if year and meta["years"] and year not in meta["years"]:
        diffs.append(("year", f"年份不同（你寫 {year}，資料庫是 {'／'.join(sorted(meta['years']))}）"))
    if meta["journals"] and not any(norm(j) and norm(j) in norm(line) for j in meta["journals"]):
        diffs.append(("journal", f"期刊名稱不同，資料庫是「{meta['journals'][0]}」"))
    return diffs


def clean_doi(raw):
    doi = raw.rstrip(".,;:")
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1].rstrip(".,;:")
    return doi


def parse_entries(ref_lines):
    """參考資料的每一筆：開頭是「作者（年份）」，或帶 DOI／PMID 的那幾行。"""
    entries = []
    for raw in ref_lines:
        line = strip_bullet(raw).strip()
        m = REF.match(line)
        doi = DOI.search(line)
        pmid = PMID.search(line)
        if not (m or doi or pmid):
            continue
        entries.append({
            "line": line,
            "author": m.group(1) if m else None,
            "year": m.group(2) if m else None,
            "doi": clean_doi(doi.group(0)) if doi else None,
            "pmid": pmid.group(1) if pmid else None,
            "url": bool(URL.search(line)),
            "cjk": bool(CJK.search(URL.sub("", line))),  # 有中文就當中文文獻（國際資料庫多半查不到）
        })
    return entries


def verify_entry(net, e):
    """回傳 {status: ok/mismatch/notfound/skipped/error/offline, diffs, meta, note}。"""
    r = {"status": None, "diffs": [], "meta": None, "note": "", "lead": "找到這篇，但書目有出入："}
    if e["doi"]:
        state, meta = lookup_doi(net, e["doi"])
        if state == "notfound" and not e["cjk"]:
            s2, m2 = search_title(net, e["line"])
            if s2 == "ok":
                r.update(status="mismatch", meta=m2, lead="",
                         diffs=[f"DOI {e['doi']} 查不到；用篇名找到一篇，它的 DOI 是 {m2['doi']}"] + [m for _, m in compare(e, m2)])
                return r
        if state == "notfound":
            r.update(status="notfound", note=f"DOI {e['doi']} 查不到，可能打錯，或這篇不存在。請貼到瀏覽器確認：https://doi.org/{e['doi']}")
            return r
    elif e["pmid"]:
        state, meta = lookup_pmid(net, e["pmid"])
        if state == "notfound":
            r.update(status="notfound", note=f"PMID {e['pmid']} 在 PubMed 查不到，可能打錯，或這篇不存在。")
            return r
    elif e["url"]:
        r.update(status="skipped", note="沒有 DOI 的網頁資料，程式不查。請自己點網址，確認還打得開、標題對。")
        return r
    elif e["cjk"]:
        r.update(status="skipped", note="中文文獻沒有 DOI，程式查不到。請到華藝或期刊網站用篇名查。")
        return r
    else:
        state, meta = search_title(net, e["line"])
        if state == "notfound":
            r.update(status="notfound", note="沒有 DOI，用篇名在 CrossRef 找不到相近的文章。請自己用篇名到 PubMed 或 Google Scholar 查。")
            return r
    if state in ("offline", "error"):
        r.update(status=state, note="這一筆查詢時連線出錯，請稍後再跑一次，或自己把 DOI 貼到瀏覽器查。")
        return r
    found = compare(e, meta)
    r.update(status="mismatch" if found else "ok", diffs=[m for _, m in found], meta=meta)
    keys = {k for k, _ in found}
    if "title" in keys and keys & {"author", "year"}:  # 篇名不同、作者或年份也不同：編號對到的是別篇
        year = "／".join(sorted(meta["years"])) or "年份不明"
        r["lead"] = ""
        r["diffs"] = [f"這個 {'DOI' if e['doi'] else 'PMID'} 查到的是另一篇文章：「{meta['title']}」（{meta['author'] or '作者不明'}，{year}）。"
                      "可能編號貼錯，或你寫的這篇不存在"]
    return r


def fetch_abstract(net, meta):
    """先用 DOI 查到的摘要；沒有就用 DOI 找 PubMed 的摘要。"""
    if meta.get("abstract"):
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", meta["abstract"]))).strip()
    pmid = meta.get("pmid")
    if not pmid and meta.get("doi"):
        state, data = net.get_json(f"{NCBI}esearch.fcgi?db=pubmed&retmode=json&tool={NCBI_TOOL}&term="
                                   + urllib.parse.quote(meta["doi"] + "[doi]"))
        ids = (data or {}).get("esearchresult", {}).get("idlist") or [] if state == "ok" else []
        pmid = ids[0] if len(ids) == 1 else ""
    if not pmid:
        return ""
    state, body = net.get(f"{NCBI}efetch.fcgi?db=pubmed&rettype=abstract&retmode=text&tool={NCBI_TOOL}&id={pmid}", accept="text/plain")
    return body.decode("utf-8", "replace").strip() if state == "ok" else ""


def label(e):
    """每筆的稱呼：作者, 年份；團體作者（年份前沒有逗號）用全名。"""
    if not e["author"]:
        return e["line"][:40]
    head = re.split(r"[（(]\d{4}", e["line"], maxsplit=1)[0].strip(" .．")
    if "," not in head and "，" not in head and "、" not in head and len(head) <= 100:
        return f"{head}, {e['year']}"
    return f"{e['author']}, {e['year']}"


LABEL = {"ok": "【相符】", "mismatch": "【不符】", "notfound": "【找不到】", "skipped": "【沒查】",
         "error": "【連線失敗】", "offline": "【連線失敗】"}


def verify_references(ref_lines, net, abstracts=False):
    entries = parse_entries(ref_lines)
    results = []
    for e in entries:
        results.append(verify_entry(net, e))
        if net.offline:
            break
    if net.offline:
        return {"offline": True, "entries": entries, "results": []}
    if abstracts:
        for r in results:
            if r["meta"]:
                r["abstract"] = fetch_abstract(net, r["meta"])
    return {"offline": False, "entries": entries, "results": results}


def verify_report(v, abstracts=False):
    if v["offline"]:
        return OFFLINE_MESSAGE
    if not v["entries"]:
        return "參考資料裡沒有看得懂的條目（作者（年份）開頭，或帶 DOI／PMID），沒有查。"
    lines = ["查文獻（用 DOI／PMID／篇名查 doi.org、CrossRef、PubMed）："]
    for i, (e, r) in enumerate(zip(v["entries"], v["results"]), 1):
        who = label(e)
        if r["status"] == "ok":
            link = f"https://doi.org/{r['meta']['doi']}" if r["meta"]["doi"] else f"PMID {r['meta']['pmid']}"
            text = f"找到，篇名、第一作者、年份、期刊都對。{link}"
        elif r["status"] == "mismatch":
            text = r["lead"] + "；".join(r["diffs"]) + "。"
        else:
            text = r["note"]
        lines.append(f"  {i}. {LABEL[r['status']]}{who}：{text}")
    counts = {k: sum(r["status"] == k for r in v["results"]) for k in LABEL}
    lines.append(f"相符 {counts['ok']}、不符 {counts['mismatch']}、找不到 {counts['notfound']}、"
                 f"沒查 {counts['skipped']}、連線失敗 {counts['error'] + counts['offline']}。")
    if counts["ok"] or counts["mismatch"]:
        lines.append("程式只確認文獻存在、書目對不對；內文寫的結論有沒有被這篇支持，要讀摘要或原文判斷。")
    if abstracts:
        for i, r in enumerate(v["results"], 1):
            if r["meta"]:
                lines.append(f"\n--- 第 {i} 筆摘要（{r['meta']['title']}）---")
                lines.append(r.get("abstract") or "（查不到摘要，請自己開 DOI 連結讀。）")
    return "\n".join(lines)


def main(argv, net=None):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if len(args) != 1 or flags - {"--offline", "--abstracts"}:
        print(__doc__)
        return 2
    with open(args[0], encoding="utf-8") as f:
        result = check(f.read())
    print(report(result))
    if result is None:
        return 2
    problems = bool(result["missing_in_refs"] or result["never_cited"])
    if "--offline" in flags:
        print("\n（--offline：沒有查文獻存不存在。）")
    else:
        v = verify_references(result["ref_lines"], net or Net(), abstracts="--abstracts" in flags)
        print()
        print(verify_report(v, abstracts="--abstracts" in flags))
        problems = problems or any(r["status"] in ("mismatch", "notfound") for r in v["results"])
    return 1 if problems else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
