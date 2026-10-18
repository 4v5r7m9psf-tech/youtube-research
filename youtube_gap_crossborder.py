"""
YouTube 調査:英語圏と日本語圏の「ずれ」を比べる

狙い:
  (A) 日本版の候補 … 英語圏では需要が大きいのに、日本語圏ではまだ作り手が少ない/小さいチャンネルでも伸びている
  (B) 海外版の候補 … 日本語圏では需要が大きいのに、英語圏ではまだ作り手が少ない/小さいチャンネルでも伸びている

同じテーマを英語と日本語のキーワードの組で検索し、言語ごとに
  ・需要:再生数の中央値
  ・入りやすさ:小さいチャンネル(登録者1万人未満)の動画のうち1万再生を超えた割合
を出して、言語の中での順位(パーセンタイル)にしてから掛け合わせる。
英語圏は市場がそもそも大きいので、金額や再生数を直接比べず、各言語の中での順位で比べるのがポイント。

使い方:環境変数 YT_API_KEY にAPIキーを入れて python3 youtube_gap_crossborder.py
出力:
  cross_summary.csv … テーマごとの英日比較とスコア
  cross_videos.csv  … 集めた動画の一覧(元データ)
APIの使用量の目安:1キーワードあたり約102ユニット × 60(30組×2言語)≒ 6,100ユニット
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

# (テーマ名, 英語キーワード, 日本語キーワード)
PAIRS = [
    ("副業", "side hustle ideas", "副業 おすすめ"),
    ("不労所得", "passive income ideas", "不労所得"),
    ("家計管理", "budgeting tips", "家計管理 コツ"),
    ("節約生活", "frugal living", "節約生活"),
    ("ミニマリスト", "minimalism lifestyle", "ミニマリスト 暮らし"),
    ("断捨離", "decluttering", "断捨離"),
    ("FIRE", "FIRE early retirement", "FIRE 早期リタイア"),
    ("高配当株", "dividend investing", "高配当株 投資"),
    ("生産性", "productivity system", "生産性 上げる 方法"),
    ("Notion", "notion setup", "Notion 使い方"),
    ("Obsidian", "obsidian setup", "Obsidian 使い方"),
    ("AIで業務効率化", "ai automation workflow", "AI 業務効率化"),
    ("Claude Code", "claude code tutorial", "Claude Code 使い方"),
    ("一緒に勉強", "study with me", "一緒に勉強しよう"),
    ("勉強法", "study tips exam", "資格 勉強法"),
    ("手帳", "journaling ideas", "手帳 使い方"),
    ("文房具", "stationery haul", "文房具 紹介"),
    ("デスクツアー", "desk setup tour", "デスクツアー"),
    ("作り置き", "meal prep", "作り置き レシピ"),
    ("お弁当", "bento lunch ideas", "お弁当 作り"),
    ("おうちカフェ", "home cafe", "おうちカフェ"),
    ("暮らしvlog", "slow living vlog", "暮らし vlog"),
    ("節約旅行", "budget travel tips", "節約旅行"),
    ("車中泊", "van life", "車中泊"),
    ("ソロキャンプ", "solo camping", "ソロキャンプ"),
    ("自給自足", "homesteading", "自給自足 生活"),
    ("タイニーハウス", "tiny house", "タイニーハウス"),
    ("定年後の仕事", "retirement jobs seniors", "定年後 仕事"),
    ("親の介護", "caring for aging parents", "親の介護"),
    ("癒しゲーム", "cozy games", "癒し ゲーム"),
]

LANGS = {
    "en": {"relevanceLanguage": "en", "regionCode": "US"},
    "ja": {"relevanceLanguage": "ja", "regionCode": "JP"},
}

DAYS_BACK = 365
VIDEOS_PER_KEYWORD = 50
SMALL_CHANNEL_SUBS = 10_000
HIT_VIEWS = 10_000
SHORTS_MAX_SECONDS = 180
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


def collect(theme, lang, keyword, key):
    published_after = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
    search = call("search", dict({
        "part": "snippet", "q": keyword, "type": "video",
        "maxResults": VIDEOS_PER_KEYWORD, "order": "relevance",
        "publishedAfter": published_after,
    }, **LANGS[lang]), key)
    total_est = search.get("pageInfo", {}).get("totalResults")
    ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
    videos, channels = {}, {}
    for part in chunks(ids):
        for v in call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(part)}, key).get("items", []):
            videos[v["id"]] = v
    for part in chunks(sorted({v["snippet"]["channelId"] for v in videos.values()})):
        for c in call("channels", {"part": "statistics", "id": ",".join(part)}, key).get("items", []):
            channels[c["id"]] = c
    rows = []
    for vid in ids:
        v = videos.get(vid)
        if not v:
            continue
        st = channels.get(v["snippet"]["channelId"], {}).get("statistics", {})
        subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0)
        secs = iso_seconds(v.get("contentDetails", {}).get("duration"))
        text = (v["snippet"]["title"] + " " + v["snippet"].get("description", "")).lower()
        rows.append({
            "theme": theme, "lang": lang, "keyword": keyword, "video_id": vid,
            "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
            "published": v["snippet"]["publishedAt"][:10],
            "format": "shorts" if ("#shorts" in text or (secs is not None and secs <= SHORTS_MAX_SECONDS)) else "long",
            "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
            "subscribers": subs,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return rows, total_est


def summarize(rows, total_est):
    valid = [r for r in rows if r["subscribers"] is not None]
    if not valid:
        return None
    small = [r for r in valid if r["subscribers"] < SMALL_CHANNEL_SUBS]
    hits = [r for r in small if r["views"] >= HIT_VIEWS]
    total_views = sum(r["views"] for r in valid) or 1
    return {
        "videos": len(valid),
        "median_views": int(statistics.median([r["views"] for r in valid])),
        "distinct_channels": len({r["channel"] for r in valid}),
        "small_videos": len(small),
        "small_hit_rate": round(len(hits) / len(small), 3) if small else 0.0,
        "small_view_share": round(sum(r["views"] for r in small) / total_views, 3),
        "shorts_share": round(sum(1 for r in valid if r["format"] == "shorts") / len(valid), 2),
        "total_results_est": total_est,
        "reliable": "yes" if len(small) >= 10 else "no",
    }


def pct_ranks(values):
    """値を0〜1の順位に直す(大きいほど1に近い)。"""
    order = sorted(values)
    n = len(values)
    if n <= 1:
        return [1.0 for _ in values]
    return [order.index(v) / (n - 1) for v in values]


def main():
    key = os.environ.get("YT_API_KEY") or input("YouTube APIキーを入力してください: ").strip()
    if not key:
        sys.exit("APIキーがありません。")

    all_rows, results = [], []
    for i, (theme, en_kw, ja_kw) in enumerate(PAIRS, 1):
        print(f"[{i}/{len(PAIRS)}] {theme}")
        res = {"theme": theme, "en_keyword": en_kw, "ja_keyword": ja_kw}
        for lang, kw in (("en", en_kw), ("ja", ja_kw)):
            rows, total = collect(theme, lang, kw, key)
            all_rows.extend(rows)
            s = summarize(rows, total) or {}
            for k, v in s.items():
                res[f"{lang}_{k}"] = v
        results.append(res)

    results = [r for r in results if "en_median_views" in r and "ja_median_views" in r]
    for lang in ("en", "ja"):
        for name, col in (("demand_pct", "median_views"), ("open_pct", "small_hit_rate")):
            ranks = pct_ranks([r[f"{lang}_{col}"] for r in results])
            for r, p in zip(results, ranks):
                r[f"{lang}_{name}"] = round(p, 2)
    for r in results:
        # 日本版の候補:英語圏の需要 × 日本語圏の入りやすさ
        r["score_to_japan"] = round(r["en_demand_pct"] * r["ja_open_pct"], 3)
        # 海外版の候補:日本語圏の需要 × 英語圏の入りやすさ
        r["score_to_overseas"] = round(r["ja_demand_pct"] * r["en_open_pct"], 3)

    fields = list(results[0].keys()) if results else ["theme"]
    with open("cross_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(results, key=lambda r: r["score_to_japan"], reverse=True))
    with open("cross_videos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()) if all_rows else ["theme"])
        w.writeheader()
        w.writerows(all_rows)

    print("\n=== 日本版の候補(英語で需要大 × 日本語で入りやすい) 上位10 ===")
    for r in sorted(results, key=lambda r: r["score_to_japan"], reverse=True)[:10]:
        print(f"{r['theme']:<12} スコア{r['score_to_japan']:.2f}  英中央値{r['en_median_views']:>10,}  日:小chヒット率{r['ja_small_hit_rate']:.0%}  信頼:{r['ja_reliable']}")
    print("\n=== 海外版の候補(日本語で需要大 × 英語で入りやすい) 上位10 ===")
    for r in sorted(results, key=lambda r: r["score_to_overseas"], reverse=True)[:10]:
        print(f"{r['theme']:<12} スコア{r['score_to_overseas']:.2f}  日中央値{r['ja_median_views']:>10,}  英:小chヒット率{r['en_small_hit_rate']:.0%}  信頼:{r['en_reliable']}")


if __name__ == "__main__":
    main()
