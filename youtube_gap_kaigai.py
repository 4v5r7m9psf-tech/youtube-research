"""
YouTube 需要と供給のずれ調査:日本語「海外の情報を日本語で解説」系

狙い:海外の情報を日本語でまとめて伝える発信のうち、どの分野に需要があって作り手が足りていないかを広く確かめる。
日本語・日本向けの検索結果で、Shortsと通常の動画を分けて集計する(1万再生・5万再生の2段階でヒットを数える)。

使い方:環境変数 YT_API_KEY にAPIキーを入れて python3 youtube_gap_kaigai.py
出力:kaigai_summary.csv / kaigai_videos.csv / kaigai_top_hits.csv
"""

import csv
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KEYWORDS = [
    # 雑学・知られていない話
    "海外 雑学",
    "海外の常識 日本の非常識",
    "世界の 知られていない",
    "海外 驚きの 仕組み",
    # ニュース・社会の解説
    "海外ニュース 解説",
    "アメリカ 社会 解説",
    "中国 経済 解説",
    "韓国 社会 事情",
    "インド 経済 解説",
    "ヨーロッパ 事情 解説",
    # 暮らし・お金
    "アメリカ 物価 生活",
    "海外 給料 比較",
    "海外 住宅事情",
    "海外 医療費",
    "海外 教育 制度",
    "東南アジア 生活 リアル",
    "海外移住 リアル",
    # ビジネス・テクノロジー
    "海外 ビジネス 事例",
    "海外 スタートアップ",
    "海外 最新 テクノロジー",
    "海外 AI 最新",
    "海外 トレンド 日本未上陸",
    "中国 最新 技術",
    "海外 便利 アイテム",
]

DAYS_BACK = 365
VIDEOS_PER_KEYWORD = 50
SMALL_CHANNEL_SUBS = 10_000
HIT_VIEWS = 50_000
SHORTS_MAX_SECONDS = 180      # 3分以下、またはタイトル等に #shorts があればShortsとみなす
LANGUAGE = "ja"
HIT_VIEWS_SMALL = 10_000      # 日本語圏は市場が小さいので、1万再生も「ヒット」として別に集計

API = "https://www.googleapis.com/youtube/v3/"


def call(endpoint, params, key):
    params = dict(params, key=key)
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if "quotaExceeded" in body:
            sys.exit("APIの1日の上限に達しました。明日もう一度実行してください。")
        sys.exit(f"APIエラー ({e.code}): {body[:500]}")


def chunks(items, n=50):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def iso_seconds(d):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return None
    days, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def is_shorts(seconds, title, description):
    text = f"{title} {description}".lower()
    if "#shorts" in text or "#short " in text or text.endswith("#short"):
        return True
    return seconds is not None and seconds <= SHORTS_MAX_SECONDS


def collect(keyword, key):
    published_after = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search = call("search", {
        "part": "snippet", "q": keyword, "type": "video",
        "maxResults": VIDEOS_PER_KEYWORD, "order": "relevance",
        "publishedAfter": published_after, "relevanceLanguage": LANGUAGE, "regionCode": "JP",
    }, key)
    video_ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
    if not video_ids:
        return []

    videos = {}
    for part in chunks(video_ids):
        res = call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(part)}, key)
        for v in res.get("items", []):
            videos[v["id"]] = v

    channel_ids = sorted({v["snippet"]["channelId"] for v in videos.values()})
    channels = {}
    for part in chunks(channel_ids):
        res = call("channels", {"part": "statistics", "id": ",".join(part)}, key)
        for c in res.get("items", []):
            channels[c["id"]] = c

    rows = []
    for vid in video_ids:
        v = videos.get(vid)
        if not v:
            continue
        stats = channels.get(v["snippet"]["channelId"], {}).get("statistics", {})
        subs = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0) or 0)
        secs = iso_seconds(v.get("contentDetails", {}).get("duration"))
        rows.append({
            "keyword": keyword,
            "format": "shorts" if is_shorts(secs, v["snippet"]["title"], v["snippet"].get("description", "")) else "long",
            "video_id": vid,
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "published": v["snippet"]["publishedAt"][:10],
            "duration_sec": secs,
            "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
            "subscribers": subs,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return rows


def summarize(keyword, fmt, rows):
    valid = [r for r in rows if r["subscribers"] is not None]
    if not valid:
        return None
    small = [r for r in valid if r["subscribers"] < SMALL_CHANNEL_SUBS]
    hits = [r for r in small if r["views"] >= HIT_VIEWS]
    hits10k = [r for r in small if r["views"] >= HIT_VIEWS_SMALL]
    ratios = [r["views"] / max(r["subscribers"], 1) for r in small]
    return {
        "keyword": keyword,
        "format": fmt,
        "videos": len(valid),
        "median_views": int(statistics.median([r["views"] for r in valid])),
        "distinct_channels": len({r["channel"] for r in valid}),
        "small_channel_videos": len(small),
        "small_channel_hits": len(hits),
        "small_hit_rate": round(len(hits) / len(small), 3) if small else 0.0,
        "small_hit_rate_10k": round(len(hits10k) / len(small), 3) if small else 0.0,
        "small_median_views_per_sub": round(statistics.median(ratios), 2) if ratios else 0.0,
        "reliable": "yes" if len(small) >= 10 else "no",
    }


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        if not rows:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    key = os.environ.get("YT_API_KEY") or input("YouTube APIキーを入力してください: ").strip()
    if not key:
        sys.exit("APIキーがありません。")

    all_rows, summaries = [], []
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"[{i}/{len(KEYWORDS)}] {kw}")
        rows = collect(kw, key)
        all_rows.extend(rows)
        for fmt in ("long", "shorts"):
            s = summarize(kw, fmt, [r for r in rows if r["format"] == fmt])
            if s:
                summaries.append(s)

    summaries.sort(key=lambda s: (s["reliable"] == "yes", s["small_hit_rate_10k"], s["small_hit_rate"]), reverse=True)

    seen, top_hits = set(), []
    for r in sorted(all_rows, key=lambda r: r["views"] / max(r["subscribers"] or 1, 1), reverse=True):
        if r["subscribers"] is None or r["subscribers"] >= SMALL_CHANNEL_SUBS or r["views"] < HIT_VIEWS_SMALL:
            continue
        if r["video_id"] in seen:
            continue
        seen.add(r["video_id"])
        top_hits.append(dict(r, views_per_sub=round(r["views"] / max(r["subscribers"], 1), 1)))

    write_csv("kaigai_videos.csv", all_rows)
    write_csv("kaigai_summary.csv", summaries)
    write_csv("kaigai_top_hits.csv", top_hits)

    print("\n=== キーワード×形式(信頼できるものを先に表示) ===")
    for s in summaries[:20]:
        print(f"{s['keyword']:<36}{s['format']:<7} 1万超{s['small_hit_rate_10k']:>6.1%} 5万超{s['small_hit_rate']:>6.1%}  小ch{s['small_channel_videos']:>3}本  中央値{s['median_views']:>10,}  信頼:{s['reliable']}")
    print(f"\n小さいチャンネルのヒット動画:{len(top_hits)}本(kaigai_top_hits.csv)")


if __name__ == "__main__":
    main()
