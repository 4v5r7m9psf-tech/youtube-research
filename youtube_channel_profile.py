"""
YouTube 調査 第3弾:ヒットした小さいチャンネルの「伸び方」を調べる

知りたいこと:新しく始めた人でも届くのか
  - チャンネルを始めてから、何本目・何日目で最初のヒット(5万再生以上)が出たか
  - どのくらいの頻度で投稿しているか
  - ヒットは1本だけのまぐれか、安定して見られているのか

使い方:
  第2弾で作られた daily_top_hits.csv と同じ場所で、
  環境変数 YT_API_KEY にAPIキーを入れて python3 youtube_channel_profile.py

出力:
  channel_profile.csv … チャンネルごとの集計
  channel_videos.csv  … 各チャンネルの全動画(公開日・再生数・長さ)

APIの使用量の目安:1チャンネルあたり数〜数十ユニット(全体で数百程度)
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

INPUT_CSV = "daily_top_hits.csv"
HIT_VIEWS = 50_000
SHORTS_MAX_SECONDS = 180
MAX_VIDEOS_PER_CHANNEL = 1000   # 動画が極端に多いチャンネルで使用量が増えすぎないための上限

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


def parse_date(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def channel_of_video(video_id, key):
    res = call("videos", {"part": "snippet", "id": video_id}, key)
    items = res.get("items", [])
    return items[0]["snippet"]["channelId"] if items else None


def fetch_channel(channel_id, key):
    res = call("channels", {"part": "snippet,statistics,contentDetails", "id": channel_id}, key)
    items = res.get("items", [])
    return items[0] if items else None


def fetch_uploads(uploads_playlist, key):
    ids, token = [], None
    while len(ids) < MAX_VIDEOS_PER_CHANNEL:
        params = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": 50}
        if token:
            params["pageToken"] = token
        res = call("playlistItems", params, key)
        ids += [it["contentDetails"]["videoId"] for it in res.get("items", [])]
        token = res.get("nextPageToken")
        if not token:
            break
    videos = []
    for part in chunks(ids):
        res = call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(part)}, key)
        for v in res.get("items", []):
            secs = iso_seconds(v.get("contentDetails", {}).get("duration"))
            text = (v["snippet"]["title"] + " " + v["snippet"].get("description", "")).lower()
            videos.append({
                "video_id": v["id"],
                "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"],
                "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
                "duration_sec": secs,
                "format": "shorts" if ("#shorts" in text or (secs is not None and secs <= SHORTS_MAX_SECONDS)) else "long",
            })
    videos.sort(key=lambda v: v["published"])
    return videos


def profile(hit_video_id, channel, videos, now=None):
    now = now or datetime.now(timezone.utc)
    if not videos:
        return None
    first = parse_date(videos[0]["published"])
    long_videos = [v for v in videos if v["format"] == "long"]

    hit_rank = next((i + 1 for i, v in enumerate(videos) if v["video_id"] == hit_video_id), None)
    hit_days = None
    if hit_rank:
        hit_days = (parse_date(videos[hit_rank - 1]["published"]) - first).days

    first50 = next(((i + 1, v) for i, v in enumerate(videos) if v["views"] >= HIT_VIEWS), None)
    recent = [v for v in videos if parse_date(v["published"]) >= now - timedelta(days=180)]
    views = [v["views"] for v in videos]
    long_views = [v["views"] for v in long_videos]

    stats = channel.get("statistics", {})
    return {
        "channel": channel["snippet"]["title"],
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "first_upload": videos[0]["published"][:10],
        "channel_age_days": (now - first).days,
        "total_videos": len(videos),
        "long_videos": len(long_videos),
        "shorts_share": round(1 - len(long_videos) / len(videos), 2),
        "uploads_last_180d": len(recent),
        "uploads_per_month_recent": round(len(recent) / 6, 1),
        "hit_video_rank": hit_rank,
        "days_first_upload_to_hit": hit_days,
        "first_50k_rank": first50[0] if first50 else None,
        "days_to_first_50k": (parse_date(first50[1]["published"]) - first).days if first50 else None,
        "videos_over_50k": sum(1 for x in views if x >= HIT_VIEWS),
        "share_over_50k": round(sum(1 for x in views if x >= HIT_VIEWS) / len(views), 2),
        "median_views_all": int(statistics.median(views)),
        "median_views_long": int(statistics.median(long_views)) if long_views else 0,
    }


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    key = os.environ.get("YT_API_KEY") or input("YouTube APIキーを入力してください: ").strip()
    if not key:
        sys.exit("APIキーがありません。")
    if not os.path.exists(INPUT_CSV):
        sys.exit(f"{INPUT_CSV} が見つかりません。第2弾の結果と同じ場所で実行してください。")

    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        hits = list(csv.DictReader(f))

    profiles, all_videos, done = [], [], set()
    for i, h in enumerate(hits, 1):
        vid = h["video_id"]
        print(f"[{i}/{len(hits)}] {h['channel']}")
        cid = channel_of_video(vid, key)
        if not cid or cid in done:
            continue
        done.add(cid)
        ch = fetch_channel(cid, key)
        if not ch:
            continue
        uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        videos = fetch_uploads(uploads, key)
        p = profile(vid, ch, videos)
        if p:
            p["hit_video_title"] = h["title"]
            p["hit_video_views"] = h["views"]
            profiles.append(p)
        for v in videos:
            all_videos.append(dict(v, channel=ch["snippet"]["title"]))

    write_csv("channel_profile.csv", profiles)
    write_csv("channel_videos.csv", all_videos)

    print("\n=== チャンネルごとの伸び方 ===")
    for p in profiles:
        print(f"{p['channel'][:28]:<30} 開始{p['first_upload']}  全{p['total_videos']:>4}本  "
              f"初ヒット:{p['first_50k_rank']}本目/{p['days_to_first_50k']}日目  "
              f"月{p['uploads_per_month_recent']}本  5万超{p['share_over_50k']:.0%}")
    ranks = [p["first_50k_rank"] for p in profiles if p["first_50k_rank"]]
    days = [p["days_to_first_50k"] for p in profiles if p["days_to_first_50k"] is not None]
    if ranks:
        print(f"\n初ヒットまでの中央値:{statistics.median(ranks)}本目、{statistics.median(days)}日目")


if __name__ == "__main__":
    main()
