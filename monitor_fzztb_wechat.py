#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://fzztb.fzsggzyjyfwzx.cn/fzis-boot"
PORTAL_BASE = "https://fzztb.fzsggzyjyfwzx.cn/portal/tenderInfo"

TARGET_PROJECT = (
    "\u798f\u5dde\u957f\u4e50\u56fd\u9645\u673a\u573a\u4e8c\u671f"
    "\u6269\u5efa\u5de5\u7a0b\u673a\u573a\u5de5\u7a0b\u822a\u7ad9"
    "\u533a\u5de5\u7a0b\u88c5\u4fee\u5de5\u7a0b\u7b2c3\u6807\u6bb5"
)

STATE_FILE = Path(os.getenv("STATE_FILE", "monitor_state.json"))


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif self._table is not None and tag == "tr":
            self._row = []
        elif self._row is not None and tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(normalize_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def zh(escaped):
    return escaped.encode("ascii").decode("unicode_escape")


def normalize_text(value):
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def get_json(path, params=None):
    url = API_BASE + path
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def query_candidate_notices(keyword):
    data = get_json(
        "/portal/ebid-ce/queryTmSecTenderList",
        {
            "pageNo": 1,
            "pageSize": 10,
            "noticeType": 4,
            "tenderProjectName": keyword,
        },
    )
    if not data.get("success"):
        raise RuntimeError(data.get("message") or "query failed")
    return data.get("result", {}).get("records", [])


def detail_url(record):
    return (
        f"{PORTAL_BASE}?bidProjectId={record.get('bidProjectId')}"
        f"&id={record.get('id')}&noticeType=4"
    )


def fetch_notice_detail(record_id):
    data = get_json("/portal/ebid-ce/tmCandidateNotice/queryById", {"id": record_id})
    if not data.get("success"):
        raise RuntimeError(data.get("message") or "detail query failed")
    return data.get("result") or {}


def extract_candidates_from_html(content):
    candidates = []
    parser = TableParser()
    parser.feed(content or "")

    candidate_title = "\u4e2d\u6807\u5019\u9009\u4eba\u540d\u79f0"
    candidate_word = "\u5019\u9009\u4eba"
    bidder_word = "\u6295\u6807\u4eba"
    rank_word = "\u6392\u540d"
    order_word = "\u6392\u5e8f"

    for table in parser.tables:
        for idx, row in enumerate(table):
            joined = " ".join(row)
            if candidate_title in joined and len(row) >= 2:
                name = next((c for c in row[1:] if c and candidate_word not in c), "")
                if name:
                    candidates.append({"rank": len(candidates) + 1, "name": name})

            has_rank_header = any(rank_word in cell or order_word in cell for cell in row)
            if has_rank_header and idx + 1 < len(table):
                headers = row
                name_idx = next(
                    (i for i, h in enumerate(headers) if candidate_word in h or bidder_word in h),
                    None,
                )
                rank_idx = next(
                    (i for i, h in enumerate(headers) if rank_word in h or order_word in h),
                    None,
                )
                if name_idx is not None:
                    for body in table[idx + 1 :]:
                        if name_idx < len(body) and body[name_idx]:
                            rank = (
                                body[rank_idx]
                                if rank_idx is not None and rank_idx < len(body)
                                else len(candidates) + 1
                            )
                            candidates.append({"rank": rank, "name": body[name_idx]})
                    break

    text = normalize_text(re.sub(r"<[^>]+>", " ", content or ""))
    pattern = "\u7b2c([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\\d]+)\u4e2d\u6807\u5019\u9009\u4eba[:\uff1a ]+([^\uff0c\u3002\uff1b;]+)"
    for rank_text, name in re.findall(pattern, text):
        candidates.append({"rank": rank_text, "name": normalize_text(name)})

    seen = set()
    deduped = []
    for item in candidates:
        key = (str(item["rank"]), item["name"])
        if item["name"] and key not in seen:
            deduped.append(item)
            seen.add(key)
    return deduped


def send_wechat(title, content):
    pushplus_token = os.getenv("PUSHPLUS_TOKEN")
    serverchan_key = os.getenv("SERVERCHAN_SENDKEY")
    wecom_webhook = os.getenv("WECOM_WEBHOOK")

    if pushplus_token:
        return post_json(
            "https://www.pushplus.plus/send",
            {
                "token": pushplus_token,
                "title": title,
                "content": content,
                "template": "txt",
            },
        )
    if serverchan_key:
        return post_json(
            f"https://sctapi.ftqq.com/{serverchan_key}.send",
            {"title": title, "desp": content},
        )
    if wecom_webhook:
        return post_json(
            wecom_webhook,
            {"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}},
        )

    print(title)
    print(content)
    print(zh("\\u672a\\u914d\\u7f6e\\u5fae\\u4fe1\\u63a8\\u9001\\u73af\\u5883\\u53d8\\u91cf\\uff0c\\u4ec5\\u6253\\u5370\\u5230\\u63a7\\u5236\\u53f0\\u3002"), file=sys.stderr)
    return ""


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"sent_ids": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def build_message(record, detail, candidates):
    project_label = zh("\\u9879\\u76ee")
    notice_name_label = zh("\\u516c\\u793a\\u540d\\u79f0")
    publish_time_label = zh("\\u53d1\\u5e03\\u65f6\\u95f4")
    default_notice_name = zh("\\u4e2d\\u6807\\u5019\\u9009\\u4eba\\u516c\\u793a")
    link_label = zh("\\u516c\\u793a\\u94fe\\u63a5")

    lines = [
        f"{project_label}:{detail.get('tenderProjectName') or record.get('tenderProjectName')}",
        f"{notice_name_label}:{detail.get('noticeName') or default_notice_name}",
        f"{publish_time_label}:{record.get('noticeSendTime') or ''}",
        "",
        zh("\\u5019\\u9009\\u4eba:"),
    ]
    if candidates:
        lines.extend(f"{c['rank']}: {c['name']}" for c in candidates)
    else:
        lines.append(zh("\\u8be6\\u60c5\\u5df2\\u53d1\\u5e03\\uff0c\\u4f46\\u811a\\u672c\\u672a\\u80fd\\u7a33\\u5b9a\\u89e3\\u6790\\u5019\\u9009\\u4eba\\u8868\\u683c\\uff0c\\u8bf7\\u6253\\u5f00\\u94fe\\u63a5\\u6838\\u5bf9\\u3002"))
    lines.extend(["", f"{link_label}:{detail_url(record)}"])
    return "\n".join(lines)


def run_once(keyword):
    state = load_state()
    records = query_candidate_notices(keyword)
    new_records = [r for r in records if r.get("id") not in state["sent_ids"]]
    if not new_records:
        print(time.strftime("%F %T"), zh("\\u6682\\u65e0\\u65b0\\u7684\\u4e2d\\u6807\\u5019\\u9009\\u4eba\\u516c\\u793a"))
        return False

    for record in reversed(new_records):
        detail = fetch_notice_detail(record["id"])
        candidates = extract_candidates_from_html(detail.get("noticeContent", ""))
        message = build_message(record, detail, candidates)
        send_wechat(zh("\\u4e2d\\u6807\\u5019\\u9009\\u4eba\\u516c\\u793a\\u5df2\\u53d1\\u5e03"), message)
        state["sent_ids"].append(record["id"])
        save_state(state)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default=os.getenv("PROJECT_KEYWORD", TARGET_PROJECT))
    parser.add_argument("--interval", type=int, default=int(os.getenv("INTERVAL_SECONDS", "300")))
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    while True:
        try:
            run_once(args.keyword)
        except Exception as exc:
            print(time.strftime("%F %T"), zh("\\u67e5\\u8be2\\u5931\\u8d25:"), exc, file=sys.stderr)
        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
