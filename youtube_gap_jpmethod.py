"""
YouTube 調査:「日本式○○」「日本の暮らし」を英語で発信する余地(公開時期を加味した版)

前回までの反省を入れた改良点:
  1. 再生数を「公開からの日数」で割った 1日あたり再生数(views_per_day)で比べる
     → 古い動画ほど強く見える偏りを減らす
  2. 期間を「直近90日」と「91〜365日前」に分けて別々に検索し、需要が増えているか減っているかを見る
     → 動画は公開直後ほど伸びやすいので、どのテーマでも直近の方が高く出る。
        そこで全テーマ共通の比率で割った「相対トレンド」で比べる(1より大きければ他より勢いがある)
  3. 「小さいチャンネル」に加えて「新しいチャンネル(開設2年以内)」が取っている再生の割合も見る
     → ヒットで登録者が1万人を超えたチャンネルが「大手」に分類されてしまう問題を補う
  4. 入りやすさの判定は、少数の動画でぶれる「ヒット率」ではなく、
     小さい/新しいチャンネルが取っている再生の割合(取り分)を中心にする

使い方:環境変数 YT_API_KEY にAPIキーを入れて python3 youtube_gap_jpmethod.py
出力:
  jpmethod_summary.csv … キーワードごとの集計(直近/過去、取り分、相対トレンド)
  jpmethod_videos.csv  … 集めた動画の一覧(元データ)
APIの使用量の目安:1キーワードあたり約210ユニット × 22 ≒ 4,600ユニット
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
    # 日本式の考え方・習慣
    "japanese method",
    "japanese decluttering",
    "japanese minimalism",
    "japanese habits",
    "japanese cleaning habits",
    "japanese home organization",
    "kaizen productivity",
    "ikigai",
    "kakeibo budgeting",
    "japanese walking method",
    "japanese eating habits",
    "japanese longevity secrets",
    "japanese parenting",
    "japanese school lunch",
    # 日本の地方・暮らし
    "rural japan life",
    "japanese countryside house",
    "akiya renovation",
    "car camping japan",
    "kei truck camping",
    "michi no eki",
    "hidden japan travel",
    "living in a small japanese town",
]

NOW = datetime.now(timezone.utc)
WINDOWS = {
    "recent": (NOW - timedelta(days=90), NOW),
    "older": (NOW - timedelta(days=365), NOW - timedelta(days=90)),
}
VIDEOS_PER_SEARCH = 50
SMALL_CHANNEL_SUBS = 10_000
NEW_CHANNEL_DAYS = 730
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


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def collect(keyword, window, key):
    after, before = WINDOWS[window]
    search = call("search", {
        "part": "snippet", "q": keyword, "type": "video",
        "maxResults": VIDEOS_PER_SEARCH, "order": "relevance",
        "publishedAfter": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publishedBefore": before.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "relevanceLanguage": "en", "regionCode": "US",
    }, key)
    ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
    videos, channels = {}, {}
    for part in chunks(ids):
        for v in call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(part)}, key).get("items", []):
            videos[v["id"]] = v
    for part in chunks(sorted({v["snippet"]["channelId"] for v in videos.values()})):
        for c in call("channels", {"part": "snippet,statistics", "id": ",".join(part)}, key).get("items", []):
            channels[c["id"]] = c
    rows = []
    for vid in ids:
        v = videos.get(vid)
        if not v:
            continue
        ch = channels.get(v["snippet"]["channelId"], {})
        st = ch.get("statistics", {})
        subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0)
        ch_created = ch.get("snippet", {}).get("publishedAt")
        ch_age = (NOW - parse_dt(ch_created)).days if ch_created else None
        published = parse_dt(v["snippet"]["publishedAt"])
        age_days = max((NOW - published).days, 1)
        views = int(v.get("statistics", {}).get("viewCount", 0) or 0)
        secs = iso_seconds(v.get("contentDetails", {}).get("duration"))
        text = (v["snippet"]["title"] + " " + v["snippet"].get("description", "")).lower()
        rows.append({
            "keyword": keyword, "window": window, "video_id": vid,
            "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
            "published": published.strftime("%Y-%m-%d"), "age_days": age_days,
            "format": "shorts" if ("#shorts" in text or (secs is not None and secs <= SHORTS_MAX_SECONDS)) else "long",
            "views": views, "views_per_day": round(views / age_days, 1),
            "subscribers": subs, "channel_age_days": ch_age,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return rows


def summarize(rows):
    valid = [r for r in rows if r["subscribers"] is not None]
    if not valid:
        return {}
    total = sum(r["views_per_day"] for r in valid) or 1
    small = [r for r in valid if r["subscribers"] < SMALL_CHANNEL_SUBS]
    new = [r for r in valid if r["channel_age_days"] is not None and r["channel_age_days"] <= NEW_CHANNEL_DAYS]
    return {
        "videos": len(valid),
        "median_vpd": round(statistics.median([r["views_per_day"] for r in valid]), 1),
        "distinct_channels": len({r["channel"] for r in valid}),
        "small_videos": len(small),
        "small_vpd_share": round(sum(r["views_per_day"] for r in small) / total, 3),
        "new_channel_videos": len(new),
        "new_channel_vpd_share": round(sum(r["views_per_day"] for r in new) / total, 3),
        "shorts_share": round(sum(1 for r in valid if r["format"] == "shorts") / len(valid), 2),
    }


def main():
    key = os.environ.get("YT_API_KEY") or input("YouTube APIキーを入力してください: ").strip()
    if not key:
        sys.exit("APIキーがありません。")

    all_rows, results = [], []
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"[{i}/{len(KEYWORDS)}] {kw}")
        res = {"keyword": kw}
        for window in ("recent", "older"):
            rows = collect(kw, window, key)
            all_rows.extend(rows)
            for k, v in summarize(rows).items():
                res[f"{window}_{k}"] = v
        results.append(res)

    ratios = [r["recent_median_vpd"] / r["older_median_vpd"]
              for r in results if r.get("older_median_vpd") and r.get("recent_median_vpd")]
    base = statistics.median(ratios) if ratios else 1.0
    for r in results:
        if r.get("older_median_vpd") and r.get("recent_median_vpd"):
            r["raw_ratio"] = round(r["recent_median_vpd"] / r["older_median_vpd"], 2)
            r["relative_trend"] = round(r["raw_ratio"] / base, 2)
        else:
            r["raw_ratio"] = r["relative_trend"] = None
        r["reliable"] = "yes" if r.get("recent_videos", 0) >= 30 and r.get("older_videos", 0) >= 30 else "no"
        # 入りやすさ:直近90日で、小さいチャンネルと新しいチャンネルが取っている再生の割合の大きい方
        r["recent_openness"] = max(r.get("recent_small_vpd_share", 0), r.get("recent_new_channel_vpd_share", 0))

    results.sort(key=lambda r: (r["reliable"] == "yes", r["recent_openness"], r.get("recent_median_vpd") or 0), reverse=True)
    fields = []
    for r in results:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open("jpmethod_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    with open("jpmethod_videos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()) if all_rows else ["keyword"])
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n全テーマ共通の 直近/過去 比率(基準):{base:.2f}")
    print("キーワード / 直近の1日あたり再生(中央値) / 直近:小ch取り分 / 直近:新ch取り分 / 相対トレンド / 信頼")
    for r in results:
        print(f"{r['keyword']:<34}{r.get('recent_median_vpd', 0):>9}  {r.get('recent_small_vpd_share', 0):>5.0%}  "
              f"{r.get('recent_new_channel_vpd_share', 0):>5.0%}  {r['relative_trend']}  {r['reliable']}")


if __name__ == "__main__":
    main()
