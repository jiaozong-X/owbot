import html
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .client import OW2Client
from config import CURRENT_SEASON

sys.stdout.reconfigure(encoding="utf-8")

RETURNED_CN_SEASONS = [CURRENT_SEASON, "21", "20", "19"]


class RankHistoryQuery(OW2Client):
    def __init__(self):
        super().__init__()
        self.role_order = ("tank", "dps", "healer", "open")
        self.role_labels = {"tank": "坦克", "dps": "输出", "healer": "辅助", "open": "开放"}

    def _normalize_season(self, season: str | None) -> str | None:
        if season is None:
            return None
        value = str(season).strip()
        if not value:
            return ""

        lowered = value.lower()
        if lowered in {"current", "now", "当前", "本赛季"}:
            return ""
        if lowered.startswith("s"):
            lowered = lowered[1:]
        if lowered.isdigit():
            return str(int(lowered))
        return None

    def _resolve_rank_info(self, role_data: dict | None) -> dict:
        role_data = role_data or {}
        current_rank_info = role_data.get("currentRankInfo") or {}
        max_rank_info = role_data.get("maxRankInfo") or {}
        if current_rank_info.get("rankName") not in (None, "", "None"):
            return current_rank_info
        return max_rank_info

    def _build_entry(self, season: str, count_data: dict) -> dict | None:
        guide_map = {item.get("roleType"): item for item in count_data.get("guideCountData", [])}
        overall = count_data.get("presetsSummaryData") or {}

        has_data = bool(guide_map) or overall.get("matchSum") not in (None, 0)
        if not has_data:
            return None

        roles = {}
        current_rank_infos = []
        for role_type in self.role_order:
            role_data = guide_map.get(role_type, {})
            rank_info = self._resolve_rank_info(role_data)
            roles[role_type] = {
                "label": self.role_labels[role_type],
                "rank": self._fmt_rank(rank_info),
                "matches": role_data.get("matchSum", 0) or 0,
                "win_rate": role_data.get("winRate", "—"),
            }
            rank_name = rank_info.get("rankName", "None")
            if rank_name not in (None, "", "None"):
                current_rank_infos.append(rank_info)

        max_rank = "—"
        if current_rank_infos:
            best = max(
                current_rank_infos,
                key=lambda item: self.rank_scores.get(item.get("rankName", "None"), 0) * 1000
                + (6 - (item.get("rankSubTier", 0) or 0)) * 100
                + (item.get("rankScore", 0) or 0),
            )
            max_rank = self._fmt_rank(best)

        return {
            "season": season,
            "season_label": "当前赛季" if season == "" else f"S{season}",
            "matches": overall.get("matchSum", 0) or 0,
            "win_rate": overall.get("winRate", "—"),
            "max_rank": max_rank,
            "roles": roles,
        }

    def query_rank_history(self, player_name: str, season: str | None = None, game_mode: str = "sport") -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        normalized_season = self._normalize_season(season)
        if season is not None and normalized_season is None:
            return {
                "player_name": player_info["name"],
                "requested_season": season,
                "entries": [],
                "invalid_season": True,
            }

        token = player_info["customerToken"]
        entries = []

        if normalized_season is not None:
            count_data = self.get_count_info(token, game_mode=game_mode, season=normalized_season)
            entry = self._build_entry(normalized_season, count_data)
            if entry:
                entries.append(entry)
            return {
                "player_name": player_info["name"],
                "requested_season": normalized_season,
                "entries": entries,
                "invalid_season": False,
            }

        current_data = self.get_count_info(token, game_mode=game_mode, season="")
        current_entry = self._build_entry("", current_data)

        # 国服回归赛季（按从新到旧排序）
        seasons = RETURNED_CN_SEASONS
        current_season = None  # 记录当前是哪个赛季

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self.get_count_info, token, game_mode, season_value): season_value
                for season_value in seasons
            }
            for future in as_completed(futures):
                season_value = futures[future]
                count_data = future.result()
                entry = self._build_entry(season_value, count_data)
                if entry:
                    entries.append(entry)
                    # 判断是否是当前赛季（数据相同）
                    if current_entry and entry.get("matches") == current_entry.get("matches"):
                        current_season = season_value

        # 如果找到当前赛季，标记并合并
        for entry in entries:
            if entry["season"] == current_season:
                entry["season_label"] = f"S{entry['season']}（当前）"
                entry["is_current"] = True
            else:
                entry["season_label"] = f"S{entry['season']}"
                entry["is_current"] = False

        # 按赛季号降序排列
        entries.sort(key=lambda item: int(item["season"]) if item["season"] else 0, reverse=True)

        return {
            "player_name": player_info["name"],
            "requested_season": None,
            "entries": entries,
            "invalid_season": False,
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        def _e(value) -> str:
            return html.escape(str(value)) if value not in (None, "") else ""

        def _win_rate_text(value) -> str:
            if value in (None, "", "—"):
                return "—"
            return f"{value}%"

        rows = ""
        for entry in data["entries"]:
            rows += (
                "<tr>"
                f'<td style="font-weight:700;color:#1C1917;">{_e(entry["season_label"])}</td>'
                f'<td style="text-align:right;color:#1C1917;">{entry["matches"]}</td>'
                f'<td style="text-align:right;color:#D4713A;font-weight:600;">{_win_rate_text(entry["win_rate"])}</td>'
                f'<td style="text-align:right;color:#1C1917;font-weight:600;">{_e(entry["max_rank"])}</td>'
                f'<td style="text-align:right;">{_e(entry["roles"]["tank"]["rank"])}</td>'
                f'<td style="text-align:right;">{_e(entry["roles"]["dps"]["rank"])}</td>'
                f'<td style="text-align:right;">{_e(entry["roles"]["healer"]["rank"])}</td>'
                f'<td style="text-align:right;">{_e(entry["roles"]["open"]["rank"])}</td>'
                "</tr>"
            )

        player_name = _e(data["player_name"])
        subtitle = "竞技历史段位"
        if data.get("requested_season") == "":
            subtitle = "当前赛季段位"
        elif data.get("requested_season"):
            subtitle = f"S{_e(data['requested_season'])} 赛季段位"

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{player_name} - 历史段位</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{--bg:#F8F6F2;--surface:#FFFFFF;--border:#E5DDD4;--border-s:#EDE8E1;--t1:#1C1917;--t2:#6B5C55;--t3:#A8968F;--accent:#D4713A;}}
        * {{margin:0;padding:0;box-sizing:border-box;}}
        body {{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--t1);padding:28px 32px;}}
        .card {{background:var(--surface);border-radius:12px;border:1px solid var(--border);}}
        .lbl {{font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:0.07em;color:var(--t3);}}
        table {{width:100%;border-collapse:collapse;white-space:nowrap;}}
        th {{text-align:right;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:var(--t3);padding:10px 12px;background:var(--bg);border-bottom:1px solid var(--border);}}
        th:first-child, td:first-child {{text-align:left;}}
        td {{padding:10px 12px;font-size:13px;color:var(--t2);border-bottom:1px solid var(--border-s);}}
        tbody tr:last-child td {{border-bottom:none;}}
    </style>
</head>
<body>
    <div class="card" style="padding:22px 24px;margin-bottom:14px;">
        <div style="font-size:22px;font-weight:700;letter-spacing:-0.02em;">{player_name}</div>
        <div style="margin-top:6px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <span class="lbl">{subtitle}</span>
            <span style="font-size:12px;color:var(--accent);background:#FBF0E8;padding:2px 8px;border-radius:999px;">season 参数已生效</span>
        </div>
    </div>

    <div class="card" style="overflow:hidden;">
        <div style="overflow-x:auto;">
            <table>
                <thead>
                    <tr>
                        <th>赛季</th>
                        <th>总场次</th>
                        <th>总胜率</th>
                        <th>最高段位</th>
                        <th>坦克</th>
                        <th>输出</th>
                        <th>辅助</th>
                        <th>开放</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>"""

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "rank_history.html")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write(html_content)
        return output_path

    def query_season_summary(self, player_name: str, game_mode: str = "sport") -> dict | None:
        """查询赛季总结，汇总所有国服赛季的段位信息"""
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        token = player_info["customerToken"]
        entries = []

        # 查询当前赛季数据，用于判断当前是哪个赛季
        current_data = self.get_count_info(token, game_mode=game_mode, season="")
        current_entry = self._build_entry("", current_data)

        # 查询历史赛季
        seasons = RETURNED_CN_SEASONS
        current_season = None  # 记录当前是哪个赛季
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self.get_count_info, token, game_mode, season_value): season_value
                for season_value in seasons
            }
            for future in as_completed(futures):
                season_value = futures[future]
                count_data = future.result()
                entry = self._build_entry(season_value, count_data)
                if entry:
                    entries.append(entry)
                    # 判断是否是当前赛季（数据相同）
                    if current_entry and entry.get("matches") == current_entry.get("matches"):
                        current_season = season_value

        # 标记当前赛季并更新标签
        for entry in entries:
            if entry["season"] == current_season:
                entry["season_label"] = f"S{entry['season']}（当前）"
                entry["is_current"] = True
            else:
                entry["season_label"] = f"S{entry['season']}"
                entry["is_current"] = False

        # 按赛季号降序排列
        entries.sort(key=lambda item: int(item["season"]) if item["season"] else 0, reverse=True)

        # 计算汇总统计
        summary = self._compute_summary(entries)

        return {
            "player_name": player_info["name"],
            "icon": player_info.get("icon", ""),
            "title": player_info.get("title", ""),
            "endorse_level": int(player_info.get("level", 0) or 0),
            "game_time": player_info.get("gameTime", "0"),
            "entries": entries,
            "summary": summary,
        }

    def _compute_summary(self, entries: list) -> dict:
        """计算赛季总结统计数据"""
        if not entries:
            return {}

        # 历史最高段位
        best_rank = None
        best_rank_score = 0
        best_rank_season = ""

        # 各职责历史最高
        role_best = {role: {"rank": "—", "season": "", "score": 0} for role in self.role_order}

        # 总场次和总胜率
        total_matches = 0
        total_wins = 0

        for entry in entries:
            # 计算最高段位
            for role_type in self.role_order:
                role_info = entry["roles"].get(role_type, {})
                rank_str = role_info.get("rank", "—")
                if rank_str and rank_str != "—":
                    # 解析段位字符串
                    score = self._parse_rank_score(rank_str)
                    if score > role_best[role_type]["score"]:
                        role_best[role_type] = {
                            "rank": rank_str,
                            "season": entry["season_label"],
                            "score": score,
                        }
                    if score > best_rank_score:
                        best_rank_score = score
                        best_rank = rank_str
                        best_rank_season = entry["season_label"]

            # 累计场次
            matches = entry.get("matches", 0) or 0
            win_rate = entry.get("win_rate", "—")
            if matches > 0 and win_rate not in ("—", None, ""):
                total_matches += matches
                try:
                    total_wins += int(matches * float(win_rate) / 100)
                except (ValueError, TypeError):
                    pass

        overall_win_rate = round(total_wins / total_matches * 100, 1) if total_matches > 0 else "—"

        return {
            "best_rank": best_rank or "—",
            "best_rank_season": best_rank_season,
            "total_matches": total_matches,
            "overall_win_rate": overall_win_rate,
            "role_best": role_best,
        }

    def _parse_rank_score(self, rank_str: str) -> int:
        """解析段位字符串为分数，用于比较"""
        if not rank_str or rank_str == "—":
            return 0

        # 段位名称映射
        rank_names = {
            "青铜": 1, "白银": 2, "黄金": 3, "铂金": 4,
            "钻石": 5, "大师": 6, "宗师": 7, "英杰": 8,
        }

        for cn_name, base_score in rank_names.items():
            if cn_name in rank_str:
                # 尝试提取段位数字
                import re
                match = re.search(r'(\d)', rank_str)
                sub_tier = int(match.group(1)) if match else 0
                # 段位越高分数越高，子段位越高分数越高
                return base_score * 1000 + (6 - sub_tier) * 100

        return 0

    def generate_season_summary_html(self, data: dict, output_path: str | None = None) -> str:
        """生成赛季总结 HTML 报告 - 竖向卡片样式"""
        def _e(value) -> str:
            return html.escape(str(value)) if value not in (None, "") else ""

        def _win_rate_text(value) -> str:
            if value in (None, "", "—"):
                return "—"
            return f"{value}%"

        from .client import url_to_data_uri

        player_name = _e(data["player_name"])
        player_icon = url_to_data_uri(data.get("icon", "")) or data.get("icon", "")
        player_title = _e(data.get("title", ""))
        endorse_level = data.get("endorse_level", 0)
        game_time = data.get("game_time", "0")
        summary = data.get("summary", {})
        entries = data.get("entries", [])

        # 汇总信息
        best_rank = _e(summary.get("best_rank", "—"))
        best_rank_season = _e(summary.get("best_rank_season", ""))
        total_matches = summary.get("total_matches", 0)
        overall_win_rate = summary.get("overall_win_rate", "—")

        # 赞誉星星
        stars = "★" * endorse_level + "☆" * (5 - endorse_level)

        # 头像处理
        avatar_fallback = player_name[:1] if player_name else "?"
        if player_icon:
            avatar_html = f'<img src="{_e(player_icon)}" style="width:64px;height:64px;border-radius:50%;border:2px solid #E5DDD4;object-fit:cover;flex-shrink:0;" alt="{player_name}">'
        else:
            avatar_html = f'<div style="width:64px;height:64px;border-radius:50%;border:2px solid #E5DDD4;background:#FBF0E8;color:#D4713A;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex-shrink:0;">{avatar_fallback}</div>'

        # 头衔
        title_html = f'<span style="font-size:12px;font-weight:500;color:#D4713A;background:#FBF0E8;padding:3px 10px;border-radius:999px;">{_e(player_title)}</span>' if player_title else ""

        # 赛季卡片 - 竖向排列
        season_cards = ""
        for entry in entries:
            season_label = _e(entry["season_label"])
            is_current = entry.get("is_current", False)
            matches = entry.get("matches", 0)
            win_rate = _win_rate_text(entry.get("win_rate", "—"))
            max_rank = _e(entry.get("max_rank", "—"))

            # 当前赛季高亮边框
            border_style = "2px solid #D4713A" if is_current else "1px solid #E5DDD4"
            current_badge = '<span style="font-size:10px;background:#D4713A;color:#fff;padding:2px 6px;border-radius:4px;margin-left:8px;">当前</span>' if is_current else ""
            
            # 各职责段位
            roles_html = ""
            for role_type in self.role_order:
                role_info = entry["roles"].get(role_type, {})
                role_label = self.role_labels[role_type]
                role_rank = _e(role_info.get("rank", "—"))
                role_color = {"tank": "#2563EB", "dps": "#D4713A", "healer": "#2F9E44", "open": "#6B5C55"}.get(role_type, "#1C1917")
                roles_html += f'''
                    <div style="flex:1;text-align:center;">
                        <div style="font-size:10px;color:#A8968F;margin-bottom:4px;">{role_label}</div>
                        <div style="font-size:14px;font-weight:600;color:{role_color};">{role_rank}</div>
                    </div>'''

            season_cards += f'''
        <div style="background:#FFFFFF;border:{border_style};border-radius:12px;padding:16px 20px;margin-bottom:12px;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
                <div style="display:flex;align-items:center;">
                    <div style="font-size:18px;font-weight:700;color:#1C1917;">{season_label}</div>
                    {current_badge}
                </div>
                <div style="font-size:22px;font-weight:700;color:#D4713A;">{max_rank}</div>
            </div>
            <div style="display:flex;gap:12px;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #EDE8E1;">
                <div style="flex:1;text-align:center;">
                    <div style="font-size:10px;color:#A8968F;margin-bottom:4px;">场次</div>
                    <div style="font-size:16px;font-weight:600;color:#1C1917;">{matches}</div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:10px;color:#A8968F;margin-bottom:4px;">胜率</div>
                    <div style="font-size:16px;font-weight:600;color:#D4713A;">{win_rate}</div>
                </div>
            </div>
            <div style="display:flex;gap:8px;">
                {roles_html}
            </div>
        </div>'''

        html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{player_name} - 赛季总结</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{--bg:#F8F6F2;--surface:#FFFFFF;--border:#E5DDD4;--border-s:#EDE8E1;--t1:#1C1917;--t2:#6B5C55;--t3:#A8968F;--accent:#D4713A;}}
        * {{margin:0;padding:0;box-sizing:border-box;}}
        body {{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--t1);padding:24px;display:flex;justify-content:center;}}
        .container {{width:420px;}}
    </style>
</head>
<body>
    <div class="container">
    <!-- 玩家信息头部 -->
    <div style="background:#FFFFFF;border:1px solid #E5DDD4;border-radius:14px;padding:20px;margin-bottom:14px;">
        <div style="display:flex;align-items:center;gap:16px;">
            {avatar_html}
            <div style="flex:1;">
                <div style="font-size:20px;font-weight:700;color:#1C1917;letter-spacing:-0.02em;">{player_name}</div>
                <div style="margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    {title_html}
                    <span style="font-size:11px;color:#A8968F;">游戏时长 {game_time}</span>
                </div>
                <div style="margin-top:6px;display:flex;align-items:center;gap:6px;">
                    <span style="font-size:13px;color:#D4713A;letter-spacing:1px;">{stars}</span>
                    <span style="font-size:10px;color:#A8968F;">赞誉 Lv.{endorse_level}</span>
                </div>
            </div>
        </div>
    </div>

    <!-- 历史最高段位汇总 -->
    <div style="background:#FFFFFF;border:1px solid #E5DDD4;border-radius:14px;padding:18px;margin-bottom:14px;">
        <div style="display:flex;align-items:center;justify-content:space-between;">
            <div>
                <div style="font-size:10px;color:#A8968F;margin-bottom:6px;">历史最高段位</div>
                <div style="font-size:28px;font-weight:700;color:#D4713A;letter-spacing:-0.02em;">{best_rank}</div>
                <div style="font-size:11px;color:#A8968F;margin-top:4px;">{best_rank_season}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:10px;color:#A8968F;margin-bottom:6px;">总场次 / 胜率</div>
                <div style="font-size:20px;font-weight:700;color:#1C1917;">{total_matches}</div>
                <div style="font-size:12px;color:#D4713A;margin-top:2px;">{overall_win_rate}%</div>
            </div>
        </div>
    </div>

    <!-- 赛季标签 -->
    <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:#A8968F;margin-bottom:10px;padding-left:4px;">赛季段位 · 国服 S19-S22</div>

    <!-- 赛季卡片列表 -->
    {season_cards}

    </div>
</body>
</html>'''

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "season_summary.html")
        with open(output_path, "w", encoding="utf-8") as file:
            file.write(html_content)
        return output_path
