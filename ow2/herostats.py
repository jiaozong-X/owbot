import html
import os
import sys

from .client import OW2Client, url_to_data_uri

sys.stdout.reconfigure(encoding="utf-8")


class HeroStatsQuery(OW2Client):
    def query_hero_stats(self, player_name: str, game_mode: str = "leisure") -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        count_data = self.get_count_info(player_info["customerToken"], game_mode=game_mode)
        mode_label = "竞技" if game_mode == "sport" else "休闲"
        heroes = []
        for h in count_data.get("presetsHeroUseSummaryList", []):
            guid = str(h.get("heroGuid", ""))
            heroes.append({
                "name": self.hero_config.get(guid, {}).get("name") or self.hero_guid_names.get(guid, "未知英雄"),
                "icon": self.hero_config.get(guid, {}).get("icon", ""),
                "matches": h.get("matchSum", 0),
                "win_rate": h.get("winRate", "0"),
                "level": h.get("heroLevel", 0),
                "kda": h.get("kda", "—"),
            })

        heroes.sort(key=lambda x: -x["matches"])

        return {
            "player_name": player_info["name"],
            "mode_label": mode_label,
            "heroes": heroes,
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        def _e(s):
            return html.escape(str(s)) if s else ""

        rows = ""
        for i, h in enumerate(data["heroes"]):
            try:
                wr = float(h["win_rate"])
            except (ValueError, TypeError):
                wr = 0.0
            wr_color = "#2F9E44" if wr >= 50 else "#B91C1C"
            bg = "#F8F6F2" if i % 2 == 0 else "#FFFFFF"
            hero_name = _e(h["name"])
            hero_icon = url_to_data_uri(h.get("icon", "")) if h.get("icon") else ""
            hero_display = (
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<img src="{hero_icon}" alt="{hero_name}" title="{hero_name}" '
                f'style="width:32px;height:32px;border-radius:9px;border:1px solid #E5DDD4;object-fit:cover;background:#F8F6F2;flex-shrink:0;">'
                f'<span>{hero_name}</span>'
                f'</div>'
                if hero_icon else
                hero_name
            )
            rows += (
                f'<tr style="background:{bg};">'
                f'<td style="color:#A8968F;width:32px;">{i + 1}</td>'
                f'<td style="font-weight:600;color:#1C1917;">{hero_display}</td>'
                f'<td style="text-align:right;">{h["matches"]}</td>'
                f'<td style="text-align:right;font-weight:600;color:{wr_color};">{h["win_rate"]}%</td>'
                f'<td style="text-align:right;color:#6B5C55;">{h["kda"]}</td>'
                f'<td style="text-align:right;color:#A8968F;">Lv.{h["level"]}</td>'
                f"</tr>"
            )

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "herostats.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        html_template = html_template.replace("{{PLAYER_NAME}}", data["player_name"])
        html_template = html_template.replace("{{MODE_LABEL}}", data["mode_label"])
        html_template = html_template.replace("{{HERO_COUNT}}", str(len(data["heroes"])))
        html_template = html_template.replace("{{HERO_ROWS}}", rows)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "herostats_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        return output_path
