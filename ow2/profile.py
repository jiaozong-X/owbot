import html
import os
import sys

from .client import OW2Client, url_to_data_uri
from config import CURRENT_SEASON

sys.stdout.reconfigure(encoding="utf-8")


class PlayerProfileQuery(OW2Client):
    def __init__(self):
        super().__init__()
        self.role_labels = {"tank": "坦克", "dps": "输出", "healer": "辅助", "open": "开放"}
        self.role_colors = {"tank": "#2563EB", "dps": "#D4713A", "healer": "#2F9E44", "open": "#6B5C55"}

    def _rank_total_score(self, info: dict | None) -> int:
        if not isinstance(info, dict):
            return 0
        rank_name = info.get("rankName", "None")
        if rank_name in (None, "", "None"):
            return 0
        rank_sub_tier = info.get("rankSubTier", 0) or 0
        rank_score = info.get("rankScore", 0) or 0
        return self.rank_scores.get(rank_name, 0) * 1000 + (6 - rank_sub_tier) * 100 + rank_score

    def _resolve_role_rank_info(self, role_data: dict | None) -> dict:
        """解析职责段位信息，返回当前段位和最高段位
        
        API 返回字段：
        - lastRankInfo: 当前段位
        - maxRankInfo: 历史最高段位
        
        兼容旧字段名：
        - currentRankInfo: 当前段位（部分接口可能使用）
        """
        role_data = role_data or {}
        # 优先使用 lastRankInfo，兼容 currentRankInfo
        last_rank_info = role_data.get("lastRankInfo") or role_data.get("currentRankInfo") or {}
        max_rank_info = role_data.get("maxRankInfo") or {}
        
        return {
            "current": last_rank_info,  # 当前段位
            "max": max_rank_info,        # 历史最高段位
        }

    def query_profile(self, player_name: str, game_mode: str = "leisure", season: str = CURRENT_SEASON) -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        season = CURRENT_SEASON if season in (None, "") else str(season)
        count_data = self.get_count_info(player_info["customerToken"], game_mode=game_mode, season=season)
        mode_label = "竞技" if game_mode == "sport" else "休闲"
        show_roles = game_mode == "sport"
        
        # 赛季标签
        season_label = f"S{season}"

        roles = []
        current_max_rank = "—"
        current_min_rank = "—"
        if show_roles:
            guide_map = {r.get("roleType"): r for r in count_data.get("guideCountData", [])}
            current_rank_infos = []
            role_order = ["tank", "dps", "healer"]
            if "open" in guide_map:
                role_order.append("open")

            for role_type in role_order:
                role_data = guide_map.get(role_type, {})
                rank_infos = self._resolve_role_rank_info(role_data)
                current_rank = rank_infos.get("current", {})
                max_rank = rank_infos.get("max", {})
                
                # 用于排序的分数（优先用当前段位，无则用最高段位）
                total_score = self._rank_total_score(current_rank) or self._rank_total_score(max_rank)
                if total_score > 0:
                    current_rank_infos.append((total_score, current_rank or max_rank))
                
                roles.append(
                    {
                        "label": self.role_labels[role_type],
                        "color": self.role_colors[role_type],
                        "rank": self._fmt_rank(current_rank or max_rank),  # 当前段位（无则显示最高段位）
                        "max_rank": self._fmt_rank(max_rank),  # 历史最高段位
                        "win_rate": role_data.get("winRate", "—"),
                        "kda": role_data.get("kda", "—"),
                        "matches": role_data.get("matchSum", 0),
                        "max_streak": role_data.get("maxWinStreak", 0),
                    }
                )

            if current_rank_infos:
                current_rank_infos.sort(key=lambda item: item[0])
                current_min_rank = self._fmt_rank(current_rank_infos[0][1])
                current_max_rank = self._fmt_rank(current_rank_infos[-1][1])

        overall = count_data.get("presetsSummaryData", {})

        top_heroes = []
        for hero in count_data.get("presetsHeroUseSummaryList", [])[:5]:
            guid = str(hero.get("heroGuid", ""))
            top_heroes.append(
                {
                    "name": self.hero_config.get(guid, {}).get("name")
                    or self.hero_guid_names.get(guid, "未知英雄"),
                    "icon": self.hero_config.get(guid, {}).get("icon", ""),
                    "matches": hero.get("matchSum", 0),
                    "win_rate": hero.get("winRate", "0"),
                    "level": hero.get("heroLevel", 0),
                }
            )

        try:
            game_time_str = f"{float(player_info.get('gameTime', '0')):.0f} 小时"
        except Exception:
            game_time_str = "—"

        endorse_level = int(player_info.get("level", 0))
        action = count_data.get("gameAction", {})

        return {
            "name": player_info["name"],
            "icon": player_info.get("icon", ""),
            "title": player_info.get("title", ""),
            "endorse_level": endorse_level,
            "game_time": game_time_str,
            "roles": roles,
            "show_roles": show_roles,
            "current_max_rank": current_max_rank,
            "current_min_rank": current_min_rank,
            "mode_label": mode_label,
            "overall": overall,
            "top_heroes": top_heroes,
            "behavior_comment": action.get("comment", ""),
            "honors_received": action.get("getHonorsCnt", 0),
            "honors_sent": action.get("sendHonorsCnt", 0),
            "reported_count": action.get("reportedCnt", 0),
            "season_label": season_label,
            "is_current_season": season == "" or season is None,
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        overall = data["overall"]

        def _i(key: str) -> str:
            return f"{int(overall.get(key, 0)):,}"

        def _e(value) -> str:
            return html.escape(str(value)) if value not in (None, "") else ""

        stars = "★" * data["endorse_level"] + "☆" * (5 - data["endorse_level"])
        behavior_color_map = {
            "优秀": "#2F9E44",
            "良好": "#2563EB",
            "一般": "#A8968F",
            "较差": "#B91C1C",
        }
        behavior_color = behavior_color_map.get(data["behavior_comment"], "#1C1917")

        role_section = ""
        if data["show_roles"]:
            rank_overview = f"""
            <div class="sec role-overview">
                <div class="card" style="padding:18px 20px;">
                    <div class="lbl" style="margin-bottom:10px;">当前最高段位</div>
                    <div style="font-size:26px;font-weight:700;color:#1C1917;letter-spacing:-0.02em;">{_e(data["current_max_rank"])}</div>
                </div>
                <div class="card" style="padding:18px 20px;">
                    <div class="lbl" style="margin-bottom:10px;">当前最低段位</div>
                    <div style="font-size:26px;font-weight:700;color:#1C1917;letter-spacing:-0.02em;">{_e(data["current_min_rank"])}</div>
                </div>
            </div>
            """

            role_cards = ""
            for role in data["roles"]:
                # 当前段位和历史最高段位
                current_rank = role.get('rank', '—')
                max_rank = role.get('max_rank', '—')
                
                # 构建段位显示区域：分两行显示当前和历史最高
                rank_display = f'''<div style="margin-bottom:12px;">
                    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px;">
                        <span style="font-size:10px;color:#A8968F;">当前</span>
                        <span style="font-size:22px;font-weight:700;color:#1C1917;letter-spacing:-0.02em;">{_e(current_rank)}</span>
                    </div>
                    <div style="display:flex;align-items:baseline;gap:8px;">
                        <span style="font-size:10px;color:#A8968F;">最高</span>
                        <span style="font-size:16px;font-weight:600;color:#6B5C55;">{_e(max_rank)}</span>
                    </div>
                </div>'''
                
                role_cards += f"""
                <div class="card" style="flex:1;padding:18px 20px;">
                    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:{role['color']};margin-bottom:10px;">{_e(role['label'])}</div>
                    {rank_display}
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 0;font-size:12px;">
                        <div><span style="color:#A8968F;">场次</span>&ensp;<b style="color:#1C1917;">{role['matches']}</b></div>
                        <div><span style="color:#A8968F;">胜率</span>&ensp;<b style="color:{role['color']};">{role['win_rate']}%</b></div>
                        <div><span style="color:#A8968F;">KDA</span>&ensp;<b style="color:#1C1917;">{_e(role['kda'])}</b></div>
                        <div><span style="color:#A8968F;">最长连胜</span>&ensp;<b style="color:#1C1917;">{role['max_streak']}</b></div>
                    </div>
                </div>"""

            role_section = rank_overview + f"""
            <div class="sec role-grid">{role_cards}</div>
            """

        stats = [
            (f"{overall.get('matchSum', 0)}", "总场次", "#1C1917"),
            (f"{overall.get('winRate', '—')}%", "总胜率", "#D4713A"),
            (f"{overall.get('matchWinSum', 0)}W {overall.get('matchLossSum', 0)}L", "胜负", "#1C1917"),
            (
                f"{int(overall.get('aveKill', 0))} / {int(overall.get('aveDeath', 0))} / {int(overall.get('aveAssist', 0))}",
                "场均 K/D/A",
                "#1C1917",
            ),
            (_i("aveHeroDamage"), "场均伤害", "#1C1917"),
            (_i("aveResistDamage"), "场均承伤", "#1C1917"),
            (_i("aveCure"), "场均治疗", "#2F9E44"),
        ]
        stat_cells = "".join(
            f'<div style="padding:14px 0;border-bottom:1px solid #EDE8E1;">'
            f'<div style="font-size:20px;font-weight:700;color:{color};letter-spacing:-0.01em;">{value}</div>'
            f'<div class="lbl" style="margin-top:3px;">{label}</div></div>'
            for value, label, color in stats
        )

        hero_rows = ""
        for index, hero in enumerate(data["top_heroes"]):
            bg = "#F8F6F2" if index % 2 == 0 else "#FFFFFF"
            hero_name = _e(hero["name"])
            hero_icon = url_to_data_uri(hero.get("icon", "")) or _e(hero.get("icon", ""))
            hero_label = (
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<img src="{hero_icon}" alt="{hero_name}" title="{hero_name}" '
                f'style="width:32px;height:32px;border-radius:9px;border:1px solid #E5DDD4;object-fit:cover;background:#F8F6F2;flex-shrink:0;">'
                f"<span>{hero_name}</span>"
                f"</div>"
                if hero_icon
                else hero_name
            )
            hero_rows += (
                f'<div style="display:flex;align-items:center;padding:9px 20px;background:{bg};">'
                f'<div style="width:20px;font-size:12px;color:#A8968F;">{index + 1}</div>'
                f'<div style="flex:1;font-size:13px;font-weight:600;color:#1C1917;">{hero_label}</div>'
                f'<div style="width:52px;text-align:right;font-size:12px;color:#6B5C55;">{hero["matches"]} 场</div>'
                f'<div style="width:60px;text-align:right;font-size:12px;font-weight:600;color:#D4713A;">{hero["win_rate"]}%</div>'
                f'<div style="width:44px;text-align:right;font-size:11px;color:#A8968F;">Lv.{hero["level"]}</div>'
                f"</div>"
            )

        title_span = (
            f'<span style="font-size:13px;font-weight:500;color:#D4713A;">{_e(data["title"])}</span>'
            if data["title"]
            else ""
        )
        avatar_src = url_to_data_uri(data.get("icon", "")) or _e(data.get("icon", ""))
        avatar_fallback = _e(data["name"][:1] if data.get("name") else "?")
        avatar_html = (
            f'<img src="{avatar_src}" style="width:60px;height:60px;border-radius:50%;border:2px solid #E5DDD4;object-fit:cover;flex-shrink:0;" '
            f'onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{{style:\'width:60px;height:60px;border-radius:50%;border:2px solid #E5DDD4;background:#FBF0E8;color:#D4713A;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:700;flex-shrink:0;\',textContent:\'{avatar_fallback}\'}}))">'
            if avatar_src
            else f'<div style="width:60px;height:60px;border-radius:50%;border:2px solid #E5DDD4;background:#FBF0E8;color:#D4713A;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:700;flex-shrink:0;">{avatar_fallback}</div>'
        )
        player_name = _e(data["name"])
        behavior_comment = _e(data["behavior_comment"])
        game_time = _e(data["game_time"])
        mode_label = _e(data["mode_label"])
        season_label = _e(data.get("season_label", "当前赛季"))
        
        # 仅当前赛季显示赞赏和举报数据
        honors_section = ""
        if data.get("is_current_season", False):
            honors_section = f"""
            <div style="display:flex;gap:12px;margin-top:6px;font-size:11px;color:#A8968F;">
                <span>收到赞赏 <b style="color:#2F9E44;">{data.get('honors_received', 0)}</b></span>
                <span>送出赞赏 <b style="color:#2563EB;">{data.get('honors_sent', 0)}</b></span>
                <span>被举报 <b style="color:#B91C1C;">{data.get('reported_count', 0)}</b></span>
            </div>"""
        hero_section = (
            "<div class='card sec' style='overflow:hidden;'>"
            "<div style='padding:14px 20px 10px;border-bottom:1px solid #EDE8E1;'>"
            f"<span class='lbl'>常用英雄（{mode_label}）</span></div>{hero_rows}</div>"
            if hero_rows
            else ""
        )

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{player_name}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{--bg:#F8F6F2;--surface:#FFFFFF;--border:#E5DDD4;--t3:#A8968F;}}
        * {{margin:0;padding:0;box-sizing:border-box;}}
        body {{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:#1C1917;padding:28px 32px 32px;}}
        .card {{background:var(--surface);border-radius:12px;border:1px solid var(--border);}}
        .sec {{margin-bottom:14px;}}
        .lbl {{font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:0.07em;color:var(--t3);}}
        .role-overview {{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}}
        .role-grid {{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;}}
        @media (max-width: 900px) {{
            body {{padding:20px;}}
            .role-overview {{grid-template-columns:1fr;}}
        }}
    </style>
</head>
<body>

    <div class="card sec" style="padding:22px 24px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;">
        {avatar_html}
        <div style="flex:1;min-width:220px;">
            <div style="font-size:20px;font-weight:700;letter-spacing:-0.02em;">{player_name}</div>
            <div style="margin-top:4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                {title_span}
                <span style="font-size:12px;color:#A8968F;">游戏时长 {game_time}</span>
                <span style="font-size:12px;color:#2563EB;background:#E8F0FE;padding:2px 8px;border-radius:999px;">{season_label}</span>
                <span style="font-size:12px;color:#D4713A;background:#FBF0E8;padding:2px 8px;border-radius:999px;">{mode_label}</span>
            </div>
        </div>
        <div style="text-align:right;flex-shrink:0;">
            <div style="font-size:18px;font-weight:700;color:{behavior_color};">{behavior_comment}</div>
            <div style="font-size:12px;color:#A8968F;margin-top:2px;">获赞 {data['honors_received']} 次</div>
            <div style="font-size:14px;color:#D4713A;margin-top:4px;letter-spacing:1px;">{stars}</div>
            <div class="lbl" style="margin-top:1px;">赞誉等级</div>
            {honors_section}
        </div>
    </div>

    {role_section}

    <div class="card sec" style="padding:0 24px;">
        <div style="padding:14px 0 0;margin-bottom:4px;"><span class="lbl">综合战绩（{season_label} · {mode_label}）</span></div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0 20px;">
            {stat_cells}
        </div>
        <div style="height:14px;"></div>
    </div>

    {hero_section}

</body>
</html>"""

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "player_profile.html")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write(html_content)
        print(f"[成功] 玩家资料已生成: {os.path.basename(output_path)}")
        return output_path
