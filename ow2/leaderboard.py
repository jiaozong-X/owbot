import html
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .client import OW2Client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_RANK_COLORS = {
    "Champion": "#FF4444",
    "Apex": "#FF4444",
    "Grandmaster": "#C084FC",
    "Master": "#F97316",
    "Diamond": "#38BDF8",
    "Platinum": "#94A3B8",
    "Gold": "#FBBF24",
    "Silver": "#9CA3AF",
    "Bronze": "#B45309",
    "None": "#6B5C55",
}


class LeaderboardQuery(OW2Client):
    def query_leaderboard(self, bindings: dict) -> list | None:
        """bindings: {user_id_str: player_name_str}"""
        if not bindings:
            return None

        def fetch_one(user_id, player_name):
            player_info = self.search_player(player_name)
            if not player_info:
                return None

            display_name = player_info.get("name") or player_name
            if not display_name:
                return None

            rank = self.get_player_rank(player_info["customerToken"])
            return {
                "user_id": user_id,
                "player_name": display_name,
                "rank": rank,
                "rank_str": self._fmt_rank(rank),
            }

        results = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(fetch_one, uid, name): uid for uid, name in bindings.items()}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        results.sort(key=lambda item: -item["rank"]["totalScore"])
        for position, entry in enumerate(results, 1):
            entry["position"] = position
        return results or None

    def generate_html_report(
        self,
        board: list,
        output_path: str | None = None,
        title: str = "群内排行榜",
        highlight_user_id: str | None = None,
        top_n: int = 50,
    ) -> str:
        visible_board = board[:top_n]
        self_entry = None
        if highlight_user_id is not None:
            target_uid = str(highlight_user_id)
            self_entry = next((entry for entry in board if str(entry.get("user_id")) == target_uid), None)

        rows = ""
        for index, entry in enumerate(visible_board, 1):
            medal = _MEDALS.get(index, str(index))
            rank = entry["rank"]
            rank_str = html.escape(entry["rank_str"])
            rank_color = _RANK_COLORS.get(rank.get("rankName", "None"), "#6B5C55")
            is_self = self_entry is not None and str(entry.get("user_id")) == str(self_entry.get("user_id"))
            bg = "#FFF1E6" if is_self else ("#FFFBF5" if index <= 3 else ("#F8F6F2" if index % 2 == 0 else "#FFFFFF"))
            pos_color = "#C2410C" if is_self else "#1C1917"
            name_color = "#9A3412" if is_self else "#1C1917"
            player_name = html.escape(entry["player_name"])
            badge = (
                '<span style="display:inline-block;margin-left:8px;padding:3px 8px;border-radius:999px;'
                'background:#D4713A;color:#FFFFFF;font-size:11px;font-weight:700;vertical-align:middle;">我的位置</span>'
                if is_self
                else ""
            )
            rows += (
                f'<tr style="background:{bg};">'
                f'<td style="text-align:center;font-size:18px;width:44px;font-weight:800;color:{pos_color};">{medal}</td>'
                f'<td style="font-weight:700;color:{name_color};">{player_name}{badge}</td>'
                f'<td style="text-align:right;font-weight:800;color:{rank_color};font-size:15px;">{rank_str}</td>'
                f"</tr>"
            )

        self_summary = ""
        if self_entry is not None:
            self_rank = self_entry["rank"]
            self_rank_color = _RANK_COLORS.get(self_rank.get("rankName", "None"), "#6B5C55")
            visible_count = min(len(board), top_n)
            status_text = (
                f"已进入前 {visible_count} 名并高亮显示"
                if self_entry["position"] <= top_n
                else f"未进入前 {top_n} 名，已在下方显示"
            )
            self_summary = (
                '<div class="card" style="padding:18px 20px;border-color:#F0B48A;background:#FFF7ED;">'
                '<div class="lbl" style="color:#C2410C;">我的排名</div>'
                '<div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-top:12px;">'
                "<div>"
                f'<div style="font-size:24px;font-weight:800;color:#7C2D12;">#{self_entry["position"]}</div>'
                f'<div style="margin-top:4px;font-size:15px;font-weight:700;color:#9A3412;">{html.escape(self_entry["player_name"])}</div>'
                f'<div style="margin-top:6px;font-size:12px;color:#C2410C;">{html.escape(status_text)}</div>'
                "</div>"
                '<div style="text-align:right;">'
                f'<div style="font-size:18px;font-weight:800;color:{self_rank_color};">{html.escape(self_entry["rank_str"])}</div>'
                '<div class="lbl" style="margin-top:4px;">当前位置</div>'
                "</div>"
                "</div>"
                "</div>"
            )

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "leaderboard.html")
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        template = template.replace("{{TITLE}}", title)
        template = template.replace("{{TOTAL_COUNT}}", str(len(board)))
        template = template.replace("{{VISIBLE_COUNT}}", str(min(len(board), top_n)))
        template = template.replace("{{ROWS}}", rows)
        template = template.replace("{{SELF_SUMMARY}}", self_summary)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "leaderboard_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(template)
        return output_path
