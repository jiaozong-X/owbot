import html
import html as escape_html
import os
import sys

from .client import OW2Client, url_to_data_uri

sys.stdout.reconfigure(encoding="utf-8")

# rankedLevel 分档（3000 分以下不展示）
_LEVEL_RANK = [
    (4500, "英杰", "#FF4444"),   # 4500+
    (4000, "宗师", "#C084FC"),   # 4000-4499
    (3500, "大师", "#F97316"),   # 3500-3999
    (3000, "钻石", "#00BFFF"),   # 3000-3499
]


def _level_to_rank(level: int) -> tuple[str, str]:
    for threshold, name, color in _LEVEL_RANK:
        if level >= threshold:
            return name, color
    return "3000以下", "#78716C"


class HeroBillboardQuery(OW2Client):
    def query_hero_billboard(self, player_name: str) -> dict | None:
        if not (self._auth.get("billboard_token") or self._auth.get("search_token")):
            return {"error": "missing_billboard_token", "player_name": player_name}

        player_info = self.search_player(player_name)
        if not player_info:
            player_info = self.get_billboard_override(player_name)
        if not player_info:
            return {"error": "player_not_found", "player_name": player_name}

        bnet_id = player_info.get("bnetId")
        if not bnet_id:
            return {"error": "missing_bnet_id", "player_name": player_name}

        raw = self.get_hero_billboard(
            int(bnet_id),
            player_info.get("roleId"),
            player_info.get("billboardToken") or player_info.get("customerToken") or player_info.get("rawCustomerToken"),
            player_info.get("billboardDts"),
            player_info.get("billboardServer"),
        )
        if not raw:
            return {
                "error": "billboard_query_failed",
                "player_name": player_info.get("name") or player_name,
                "bnet_id": int(bnet_id),
                "source": player_info.get("_source", "search"),
            }

        def parse_list(items: list) -> list:
            result = []
            for item in items:
                level = item.get("rankedLevel", 0)
                guid = str(item.get("heroGuid", ""))
                hero_info = self.hero_config.get(guid, {})
                rank_name, rank_color = _level_to_rank(level)
                result.append({
                    "hero":       self.hero_guid_names.get(guid, "未知英雄"),
                    "hero_icon":  url_to_data_uri(hero_info.get("icon", "")) if hero_info.get("icon") else "",
                    "rank_num":   item.get("rankNum", 0),
                    "level":      level,
                    "rank_name":  rank_name,
                    "rank_color": rank_color,
                    "matches":    item.get("matchSum", 0),
                    "win_rate":   item.get("winRate", 0.0),
                })
            result.sort(key=lambda x: -x["level"])
            return result

        return {
            "player_name": player_info["name"],
            "player_icon": url_to_data_uri(player_info.get("icon", "")) if player_info.get("icon") else "",
            "open":   parse_list(raw.get("sportOpenHeroBillboardList", [])),
            "preset": parse_list(raw.get("sportPresetHeroBillboardList", [])),
            "source": player_info.get("_source", "search"),
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        def make_rows(heroes: list) -> str:
            rows = ""
            for i, h in enumerate(heroes):
                wr = h["win_rate"]
                wr_color = "#2F9E44" if wr >= 50 else ("#B91C1C" if wr > 0 else "#A8968F")
                bg = "#F8F6F2" if i % 2 == 0 else "#FFFFFF"
                hero_name = escape_html.escape(str(h["hero"]))
                hero_icon = escape_html.escape(str(h.get("hero_icon", "")))
                hero_cell = (
                    f'<div style="display:flex;align-items:center;gap:8px;min-width:0;">'
                    f'<img src="{hero_icon}" alt="{hero_name}" title="{hero_name}" '
                    f'style="width:30px;height:30px;border-radius:7px;object-fit:cover;background:#EDE8E1;border:1px solid #E5DDD4;flex:0 0 auto;">'
                    f'<span style="font-weight:600;color:#1C1917;">{hero_name}</span>'
                    f'</div>'
                    if hero_icon else
                    f'<span style="font-weight:600;color:#1C1917;">{hero_name}</span>'
                )
                rows += (
                    f'<tr style="background:{bg};">'
                    f'<td style="color:#A8968F;width:32px;">{i+1}</td>'
                    f'<td>{hero_cell}</td>'
                    f'<td style="text-align:right;">'
                    f'  <span style="font-weight:700;color:{h["rank_color"]};">{h["rank_name"]}</span>'
                    f'  <span style="font-size:11px;color:#A8968F;margin-left:4px;">{h["level"]}</span>'
                    f'</td>'
                    f'<td style="text-align:right;font-weight:700;color:#D4713A;">#{h["rank_num"]}</td>'
                    f'<td style="text-align:right;">{h["matches"]}</td>'
                    f'<td style="text-align:right;font-weight:600;color:{wr_color};">'
                    f'{"—" if h["matches"] == 0 else f"{wr:.1f}%"}'
                    f'</td>'
                    f'</tr>'
                )
            return rows or '<tr><td colspan="6" style="text-align:center;color:#A8968F;padding:20px;">暂无英雄排名数据</td></tr>'

        open_rows   = make_rows(data["open"])
        preset_rows = make_rows(data["preset"])

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "herobillboard.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()

        html = html.replace("{{PLAYER_NAME}}", data["player_name"])
        html = html.replace("{{PLAYER_ICON}}", data.get("player_icon") or "")
        html = html.replace("{{OPEN_COUNT}}", str(len(data["open"])))
        html = html.replace("{{PRESET_COUNT}}", str(len(data["preset"])))
        html = html.replace("{{OPEN_ROWS}}", open_rows)
        html = html.replace("{{PRESET_ROWS}}", preset_rows)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "herobillboard_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path
