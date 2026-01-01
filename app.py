import json
import math
from datetime import datetime, timedelta, timezone
import pandas as pd
import altair as alt
import streamlit as st
import googleapiclient.discovery

# =====================
# Streamlit 初期設定
# =====================
st.set_page_config(page_title="YouTube 動画分析", layout="wide")
st.title("YouTube：再生数 × いいね数")

# =====================
# API 初期化
# =====================
with open("youtube_api_key.json", "r", encoding="utf-8") as f:
    config = json.load(f)

# api_key = config["api_key"]

# Streamlitクラウド用
api_key = st.secrets["YOUTUBE_API_KEY"]

youtube = googleapiclient.discovery.build(
    "youtube", "v3", developerKey=api_key
)

# =====================
# ① uploads playlist ID 取得
# =====================
@st.cache_data(ttl=24 * 60 * 60)
def get_uploads_playlist_id(channel_id):
    res = youtube.channels().list(
        part="contentDetails",
        id=channel_id
    ).execute()

    if "items" not in res or len(res["items"]) == 0:
        raise ValueError("チャンネルIDが不正です")

    return res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

# =====================
# ② 最近X日以内の動画ID取得
# =====================
@st.cache_data(ttl=60 * 30)
def get_recent_video_ids(uploads_playlist_id, days=7):
    published_after = datetime.now(timezone.utc) - timedelta(days=days)

    video_ids = []
    next_page_token = None

    while True:
        res = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()

        for item in res["items"]:
            published_at = datetime.fromisoformat(
                item["snippet"]["publishedAt"].replace("Z", "+00:00")
            )

            if published_at < published_after:
                return video_ids  # quota節約のため即終了

            video_ids.append(item["snippet"]["resourceId"]["videoId"])

        next_page_token = res.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids

# =====================
# ③ 再生数・いいね取得
# =====================
@st.cache_data(ttl=60 * 30)
def get_video_stats(video_ids):
    if not video_ids:
        return pd.DataFrame()

    rows = []

    # 50件ずつ分割
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]

        res = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(chunk)
        ).execute()

        for item in res.get("items", []):
            rows.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "view_count": int(item["statistics"].get("viewCount", 0)),
                "like_count": int(item["statistics"].get("likeCount", 0)),
                "published_at": item["snippet"]["publishedAt"]
            })

    return pd.DataFrame(rows)

# =====================
# UI（入力）
# =====================
CHANNEL_OPTIONS = {
    "NHK MUSIC": "UC8T8_deSUS97DWZeKO_TL9Q",
    "NHK": "UCip8ve30-AoX2y2OtAAmqFA",
}
channel_name = st.selectbox(
    "分析するチャンネルを選択",
    list(CHANNEL_OPTIONS.keys())
)

CHANNEL_ID = CHANNEL_OPTIONS[channel_name]

days = st.slider("何日前まで取得するか", 1, 15, 5)

if st.button("分析する"):
    try:
        with st.spinner("動画情報を取得中..."):
            uploads_id = get_uploads_playlist_id(CHANNEL_ID)
            video_ids = get_recent_video_ids(uploads_id, days=days)
            df = get_video_stats(video_ids)

        if df.empty:
            st.warning("指定期間内の動画がありません")
            st.stop()
            
        st.success(f"{len(df)} 本の動画を取得しました")

        # =====================
        # 表示：テーブル
        # =====================
        st.subheader("再生数トップ3")
        
        df['thumbnail'] = df['video_id'].apply(
            lambda x: f"https://img.youtube.com/vi/{x}/hqdefault.jpg"
        )
        df["video_url"] = "https://www.youtube.com/watch?v=" + df["video_id"]
        df["published_at"] = pd.to_datetime(df["published_at"])
        df["like_rate"] = df["like_count"] / df["view_count"]

        # 再生数順にソートして上位3本を取得
        top3_df = df.sort_values("view_count", ascending=False).head(3)

        for _, row in top3_df.iterrows():
            cols = st.columns([1, 4, 1, 1])
            cols[0].image(row["thumbnail"], width=100)
            cols[1].markdown(f"[{row['title']}]({row['video_url']})")
            cols[2].metric("再生数", f"{row['view_count']:,}")
            cols[3].metric("いいね", f"{row['like_count']:,}")



        # =====================
        # 表示：散布図
        # =====================
        
        # Altairによる散布図
        st.subheader("再生数 × いいね数")
        
        view_min_val = math.floor(df["view_count"].min() / 100) * 100
        view_max_val = math.ceil(df["view_count"].max() / 100) * 100

        view_min = alt.param(
            name="view_min",
            value=view_min_val,
            bind=alt.binding_range(
                min=100,
                max=view_max_val,
                step=100,
                name="再生数 最小："
            )
        )
        view_max = alt.param(
            name="view_max",
            value=view_max_val,
            bind=alt.binding_range(
                min=100,
                max=view_max_val,
                step=100,
                name="再生数 最大："
            )
        )

        chart = (
            alt.Chart(df)
            .add_params(view_min, view_max)
            .transform_filter(
                (alt.datum.view_count >= view_min) &
                (alt.datum.view_count <= view_max)
            )
            .mark_circle(size=120)
            .encode(
                x=alt.X("view_count:Q", scale=alt.Scale(type="log", nice=True), title="再生回数"),
                y=alt.Y("like_count:Q", scale=alt.Scale(type="log", nice=True), title="いいね数"),
                color=alt.Color(
                    "like_rate:Q",
                    scale=alt.Scale(scheme="redblue"),
                    legend=alt.Legend(title="いいね率")
                ),
                tooltip=[
                    alt.Tooltip("title:N", title="タイトル"),
                    alt.Tooltip("view_count:Q", title="再生回数"),
                    alt.Tooltip("like_count:Q", title="いいね数"),
                    alt.Tooltip("like_rate:Q", title="いいね率", format=".2%"),
                    alt.Tooltip("published_at:T", title="公開日")
                ],
            )
            .configure_axis(grid=True)
        )

        st.altair_chart(chart, use_container_width=True)

        st.caption(
            "※ スライダーは実数値（線形）です。"
            " グラフの軸は対数表示のため、同じ移動量でも見た目の変化は一定ではありません。"
        )

        st.subheader("動画一覧")
        st.dataframe(
            df[["thumbnail", "title", "view_count", "like_count", "video_url"]],
            use_container_width=True,
            column_config={
                "thumbnail": st.column_config.ImageColumn("サムネイル"),
                "title": st.column_config.TextColumn("タイトル")
            },
            hide_index=True
        )


    except Exception as e:
        st.error(str(e))



