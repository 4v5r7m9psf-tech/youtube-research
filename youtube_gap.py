"""
YouTube 需要と供給のずれ調査スクリプト

考え方:
  登録者が少ないチャンネルの動画が、たくさん再生されているテーマ
  = 見たい人(需要)に対して、作る人(供給)が足りていないテーマ

使い方:
  1. YouTube Data API v3 の APIキーを用意する
  2. 下の KEYWORDS を見たいテーマに書き換える(英語のほうが海外向けの結果になる)
  3. python3 youtube_gap.py を実行し、APIキーを入力する
     (環境変数 YT_API_KEY に入れておけば入力不要)

出力:
  gap_summary.csv  … テーマごとの集計(ずれが大きい順)
  gap_videos.csv   … 集めた動画の一覧(元データ。数字の検証用)

APIの使用量の目安:
  1キーワードあたり約102ユニット(検索100+詳細取得2)。
  無料枠は1日10,000ユニットなので、1回に90キーワード程度まで。
  上限に達するとエラーで止まるので、翌日に続きを実行すればよい。
"""

import csv
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== 設定(ここを書き換える) =====

KEYWORDS = [
    # 旅行
    "japan travel tips",
    "japan hidden gems",
    "rural japan travel",
    "kyushu travel",
    "japan solo travel",
    "japan train travel",
    "onsen japan",
    # 食
    "japanese convenience store food",
    "japanese supermarket",
    "japanese home cooking",
    "matcha",
    "japanese whisky",
    "sake",
    "wagyu",
    "japanese snacks",
    # モノ・コレクション
    "pokemon cards japan",
    "vintage camera japan",
    "japanese knives",
    "japanese stationery",
    "japanese pottery",
    "japanese skincare",
    # 暮らし
    "living in japan",
    "moving to japan",
    "cost of living japan",
    "japanese countryside life",
    "akiya japan",
    "working in japan",
    "japanese tax foreigner",
]

DAYS_BACK = 365           # 何日前までに公開された動画を見るか
VIDEOS_PER_KEYWORD = 50   # 1キーワードあたりの動画数(最大50)
SMALL_CHANNEL_SUBS = 10_000   # これ未満の登録者数を「小さいチャンネル」とみなす
HIT_VIEWS = 50_000            # これ以上の再生数を「ヒット」とみなす
LANGUAGE = "en"           # 結果を寄せる言語

# ===== ここから下は通常は触らなくてよい =====

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


def collect(keyword, key):
    published_after = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search = call("search", {
        "part": "snippet",
        "q": keyword,
        "type": "video",
        "maxResults": VIDEOS_PER_KEYWORD,
        "order": "relevance",
        "publishedAfter": published_after,
        "relevanceLanguage": LANGUAGE,
    }, key)

    items = search.get("items", [])
    video_ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
    if not video_ids:
        return []

    videos = {}
    for part in chunks(video_ids):
        res = call("videos", {"part": "snippet,statistics", "id": ",".join(part)}, key)
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
        ch = channels.get(v["snippet"]["channelId"], {})
        stats = ch.get("statistics", {})
        if stats.get("hiddenSubscriberCount"):
            subs = None
        else:
            subs = int(stats.get("subscriberCount", 0) or 0)
        rows.append({
            "keyword": keyword,
            "video_id": vid,
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "published": v["snippet"]["publishedAt"][:10],
            "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
            "subscribers": subs,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return rows


def summarize(keyword, rows):
    valid = [r for r in rows if r["subscribers"] is not None]
    if not valid:
        return None
    small = [r for r in valid if r["subscribers"] < SMALL_CHANNEL_SUBS]
    small_hits = [r for r in small if r["views"] >= HIT_VIEWS]
    views = [r["views"] for r in valid]
    ratios = [r["views"] / max(r["subscribers"], 1) for r in small]
    return {
        "keyword": keyword,
        "videos": len(valid),
        "median_views": int(statistics.median(views)),
        "distinct_channels": len({r["channel"] for r in valid}),
        "small_channel_videos": len(small),
        "small_channel_hits": len(small_hits),
        # 小さいチャンネルの動画のうちヒットした割合(ずれの主指標)
        "small_hit_rate": round(len(small_hits) / len(small), 3) if small else 0.0,
        # 小さいチャンネルの「再生数 ÷ 登録者数」の中央値(登録者の何倍見られているか)
        "small_median_views_per_sub": round(statistics.median(ratios), 2) if ratios else 0.0,
    }


def main():
    key = os.environ.get("YT_API_KEY") or input("YouTube APIキーを入力してください: ").strip()
    if not key:
        sys.exit("APIキーがありません。")

    all_rows, summaries = [], []
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"[{i}/{len(KEYWORDS)}] {kw}")
        rows = collect(kw, key)
        all_rows.extend(rows)
        s = summarize(kw, rows)
        if s:
            summaries.append(s)

    summaries.sort(key=lambda s: (s["small_hit_rate"], s["small_median_views_per_sub"]), reverse=True)

    with open("gap_videos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()) if all_rows else ["keyword"])
        w.writeheader()
        w.writerows(all_rows)

    with open("gap_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0].keys()) if summaries else ["keyword"])
        w.writeheader()
        w.writerows(summaries)

    print("\n=== ずれが大きいテーマ(上位10) ===")
    print("テーマ / 小チャンネルのヒット率 / 登録者の何倍見られているか / 再生数の中央値")
    for s in summaries[:10]:
        print(f"{s['keyword']:<35} {s['small_hit_rate']:>6.1%}  {s['small_median_views_per_sub']:>6}倍  {s['median_views']:>10,}")
    print("\n詳細: gap_summary.csv / 元データ: gap_videos.csv")


if __name__ == "__main__":
    main()
