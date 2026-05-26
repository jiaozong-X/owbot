import html
import os
import sys

from .client import OW2Client

sys.stdout.reconfigure(encoding="utf-8")


class MapStatsQuery(OW2Client):
    def query_map_stats(self, player_name: str, game_mode: str = "leisure") -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        matches = self.get_match_list(player_info["customerToken"], game_mode=game_mode)
        if not matches:
            return None

        stats: dict[str, dict] = {}
        for m in matches:
            map_name = self.map_names.get(str(m.get("mapGuid", "")), "未知地图")
            if map_name not in stats:
                stats[map_name] = {"total": 0, "wins": 0}
            stats[map_name]["total"] += 1
            if m.get("matchRet", 0) == 1:
                stats[map_name]["wins"] += 1

        maps = sorted(
            [
                {
                    "map_name": name,
                    "total": s["total"],
                    "wins": s["wins"],
                    "losses": s["total"] - s["wins"],
                    "win_rate": s["wins"] / s["total"] * 100,
                }
                for name, s in stats.items()
            ],
            key=lambda x: -x["total"],
        )

        return {
            "player_name": player_info["name"],
            "maps": maps,
            "total_matches": len(matches),
            "game_mode": game_mode,
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        def _e(s): return html.escape(str(s)) if s else ""  # HTML 转义辅助函数
        mode_label = "竞技" if data["game_mode"] == "sport" else "休闲"

        rows = ""
        for m in data["maps"]:
            wr = m["win_rate"]
            bar_color = "#2F9E44" if wr >= 50 else "#B91C1C"
            wr_color  = "#2F9E44" if wr >= 50 else "#B91C1C"
            rows += (
                f'<div class="map-row">'
                f'<span class="map-name">{_e(m["map_name"])}</span>'
                f'<span class="num num-total">{m["total"]}</span>'
                f'<span class="num num-win">{m["wins"]}</span>'
                f'<span class="num num-lose">{m["losses"]}</span>'
                f'<div class="bar-wrap">'
                f'  <div class="bar-track"><div class="bar-fill" style="width:{wr:.1f}%;background:{bar_color};"></div></div>'
                f'  <span class="bar-pct" style="color:{wr_color};">{wr:.0f}%</span>'
                f'</div>'
                f'</div>'
            )

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "mapstats.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        html_template = html_template.replace("{{PLAYER_NAME}}", _e(data["player_name"]))
        html_template = html_template.replace("{{TOTAL_MATCHES}}", str(data["total_matches"]))
        html_template = html_template.replace("{{MODE_LABEL}}", mode_label)
        html_template = html_template.replace("{{MAP_ROWS}}", rows)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "mapstats_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        return output_path
