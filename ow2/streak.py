import sys

from .client import OW2Client

sys.stdout.reconfigure(encoding="utf-8")


class StreakQuery(OW2Client):
    def query_streak(self, player_name: str, game_mode: str = "leisure") -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        matches = self.get_match_list(player_info["customerToken"], game_mode=game_mode)
        if not matches:
            return None

        results = [m.get("matchRet", 0) for m in matches]

        # 计算当前连胜/连败数
        first = results[0]
        count = 0
        for r in results:
            if r == first:
                count += 1
            else:
                break

        return {
            "player_name": player_info["name"],
            "streak_type": "连胜" if first == 1 else "连败",
            "count": count,
            "recent": ["胜" if r == 1 else "负" for r in results[:10]],
            "game_mode": game_mode,
        }

    def format_message(self, data: dict) -> str:
        emoji = "🏆" if data["streak_type"] == "连胜" else "💀"
        recent_str = " ".join(data["recent"])
        mode = "竞技" if data["game_mode"] == "sport" else "休闲"
        return (
            f"{emoji} {data['player_name']}（{mode}）"
            f"当前{data['streak_type']} {data['count']} 场\n"
            f"近{len(data['recent'])}场：{recent_str}"
        )

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        import html as _html
        import os as _os

        _e = lambda v: _html.escape(str(v)) if v not in (None, "") else ""

        player_name = _e(data["player_name"])
        streak_type = data["streak_type"]
        count = data["count"]
        recent = data["recent"]
        mode = "竞技" if data.get("game_mode") == "sport" else "休闲"

        is_win = streak_type == "连胜"
        emoji = "🏆" if is_win else "💀"
        accent = "#D4713A" if is_win else "#B91C1C"
        accent_bg = "#FBF0E8" if is_win else "#FFF5F5"
        gradient = "linear-gradient(135deg, #FBF0E8, #FFF7ED)" if is_win else "linear-gradient(135deg, #FFF5F5, #FFF1F0)"

        # Build recent match dots
        dots = ""
        for r in recent:
            if r == "胜":
                dots += '<span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:8px;background:#EBFBEE;color:#2B7A3D;font-size:13px;font-weight:700;margin-right:6px;">W</span>'
            else:
                dots += '<span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:8px;background:#FFF5F5;color:#B91C1C;font-size:13px;font-weight:700;margin-right:6px;">L</span>'

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{player_name} - {streak_type}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: #F8F6F2;
            color: #1C1917;
            padding: 28px 32px 32px;
        }}
        .card {{
            background: #FFFFFF;
            border-radius: 16px;
            border: 1px solid #E5DDD4;
            padding: 32px 36px;
            text-align: center;
        }}
        .streak-emoji {{ font-size: 48px; margin-bottom: 8px; }}
        .streak-count {{
            font-size: 64px;
            font-weight: 700;
            letter-spacing: -0.03em;
            color: {accent};
            line-height: 1.1;
        }}
        .streak-label {{
            font-size: 14px;
            font-weight: 600;
            color: #6B5C55;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-top: 4px;
        }}
        .meta {{
            margin-top: 20px;
            font-size: 13px;
            color: #A8968F;
        }}
        .meta span {{
            background: {accent_bg};
            color: {accent};
            padding: 3px 12px;
            border-radius: 999px;
            font-weight: 600;
            margin: 0 4px;
        }}
        .dots-section {{
            margin-top: 28px;
            padding-top: 24px;
            border-top: 1px solid #EDE8E1;
        }}
        .dots-label {{
            font-size: 10px;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.07em;
            color: #A8968F;
            margin-bottom: 12px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="streak-emoji">{emoji}</div>
        <div style="font-size:18px;font-weight:700;color:#1C1917;letter-spacing:-0.02em;margin-bottom:6px;">{player_name}</div>
        <div class="streak-count">{count}<span style="font-size:24px;"> 场</span></div>
        <div class="streak-label">{streak_type}中</div>
        <div class="meta">
            模式 <span>{mode}</span> · 近 {len(recent)} 场战绩
        </div>
        <div class="dots-section">
            <div class="dots-label">近期战绩</div>
            <div style="display:flex;justify-content:center;flex-wrap:wrap;">{dots}</div>
        </div>
    </div>
</body>
</html>"""

        if output_path is None:
            output_path = _os.path.join(_os.path.dirname(__file__), "..", "streak_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        return output_path
