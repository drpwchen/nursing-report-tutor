"""對引用：檢查報告「內文引用」和「參考資料」有沒有對上。

這是一支程式，不是 AI：同一份報告丟進來，每次答案都一樣。
它只對「作者姓氏＋年份」，不判斷文獻是真是假、寫得好不好——那些要人自己查。

用法：
    python check_citations.py 報告.txt

報告裡要有一行標題「參考資料」（或「參考文獻」「伍、參考文獻」、References），上面是內文、下面是參考資料，一行一筆。
內文引用支援 APA 常見寫法：(Example, 2021)、(Example & Sample, 2020)、(Example et al., 2019)、
(Example, 2021; Sample, 2020)、Example (2021)、（陳, 2022）、（王小明等，2016）。
中文只寫姓（例：（王等，2016））時，對到同年、同姓開頭的參考資料就算對上。
"""

import re
import sys

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


def clean_name(name):
    """中文名字後面的「等」「等人」不算名字。"""
    for tail in ("等人", "等"):
        if len(name) > len(tail) and name.endswith(tail) and "一" <= name[0] <= "鿿":
            return name[: -len(tail)]
    return name


def same_source(cited, listed):
    """作者、年份都一樣才算對上；中文只寫姓時，同姓開頭就算。"""
    (a, ya), (b, yb) = cited, listed
    if ya != yb:
        return False
    return a == b or ("一" <= a[0] <= "鿿" and b.startswith(a))


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


def reference_entries(ref_lines):
    found = set()
    for line in ref_lines:
        m = REF.match(line.lstrip("*-•·> \t"))  # 粗體、清單符號不算
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
        lines.append("全部對上了。（程式只對作者和年份；每一篇是不是真的、內容有沒有引錯，還是要你自己查原文。）")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        result = check(f.read())
    print(report(result))
    if result is None:
        return 2
    return 0 if not result["missing_in_refs"] and not result["never_cited"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
