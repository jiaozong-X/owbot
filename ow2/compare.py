import html
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from .client import OW2Client, url_to_data_uri

sys.stdout.reconfigure(encoding="utf-8")

_ROLE_LABELS = {"tank": "坦克", "dps": "输出", "healer": "支援", "open": "开放"}
_ROLE_COLORS = {"tank": "#2563EB", "dps": "#D4713A", "healer": "#2F9E44", "open": "#6B5C55"}


class CompareQuery(OW2Client):
    def query_compare(self, player_a: str, player_b: str) -> dict | None:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fa = ex.submit(self._get_player_data, player_a)
            fb = ex.submit(self._get_player_data, player_b)
            data_a, data_b = fa.result(), fb.result()

        if not data_a or not data_b:
            return None
        return {"a": data_a, "b": data_b}

    def _get_player_data(self, player_name: str) -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        count_data = self.get_count_info(player_info["customerToken"])
        guide_map = {r.get("roleType"): r for r in count_data.get("guideCountData", [])}
        ov = count_data.get("presetsSummaryData", {})

        role_order = ["tank", "dps", "healer"]
        if "open" in guide_map:
            role_order.append("open")

        roles = {}
        for role in role_order:
            r = guide_map.get(role, {})
            info = r.get("currentRankInfo") or {}
            if info.get("rankName") in (None, "", "None"):
                info = r.get("maxRankInfo", {}) or {}
            roles[role] = {
                "rank": self._fmt_rank(info),
                "win_rate": r.get("winRate", "—"),
                "kda": r.get("kda", "—"),
                "matches": r.get("matchSum", 0),
            }

        return {
            "name": player_info["name"],
            "icon": player_info.get("icon", ""),
            "roles": roles,
            "matches": ov.get("matchSum", 0),
            "win_rate": ov.get("winRate", "—"),
            "kda": f"{int(ov.get('aveKill', 0))}/{int(ov.get('aveDeath', 0))}/{int(ov.get('aveAssist', 0))}",
            "avg_damage": int(ov.get("aveHeroDamage", 0)),
            "avg_heal": int(ov.get("aveCure", 0)),
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        def _e(s):
            return html.escape(str(s)) if s else ""

        a, b = data["a"], data["b"]

        def icon(p):
            uri = url_to_data_uri(p["icon"])
            return (
                f'<img src="{uri}" style="width:48px;height:48px;border-radius:50%;'
                f'border:2px solid #E5DDD4;object-fit:cover;" onerror="this.style.display=\'none\'">'
            )

        def stat_row(label, va, vb, higher_is_better=True):
            try:
                fa_val = float(str(va).replace("%", "").replace(",", ""))
                fb_val = float(str(vb).replace("%", "").replace(",", ""))
                if higher_is_better:
                    ca = "#2F9E44" if fa_val > fb_val else ("#B91C1C" if fa_val < fb_val else "#1C1917")
                    cb = "#2F9E44" if fb_val > fa_val else ("#B91C1C" if fb_val < fa_val else "#1C1917")
                else:
                    ca = "#2F9E44" if fa_val < fb_val else ("#B91C1C" if fa_val > fb_val else "#1C1917")
                    cb = "#2F9E44" if fb_val < fa_val else ("#B91C1C" if fb_val > fa_val else "#1C1917")
            except (ValueError, TypeError):
                ca = cb = "#1C1917"
            return (
                f'<tr>'
                f'<td style="text-align:right;font-weight:600;color:{ca};">{va}</td>'
                f'<td style="text-align:center;font-size:10px;font-weight:600;text-transform:uppercase;'
                f'letter-spacing:.07em;color:#A8968F;padding:10px 16px;">{label}</td>'
                f'<td style="font-weight:600;color:{cb};">{vb}</td>'
                f'</tr>'
            )

        def win_rate_text(value):
            if value in (None, "", "—"):
                return "—"
            return f"{value}%"

        role_rows = ""
        role_order = [role for role in ("tank", "dps", "healer", "open") if role in a["roles"] or role in b["roles"]]
        for role in role_order:
            color = _ROLE_COLORS[role]
            label = _ROLE_LABELS[role]
            ra = a["roles"].get(role, {"rank": "—", "win_rate": "—", "matches": 0})
            rb = b["roles"].get(role, {"rank": "—", "win_rate": "—", "matches": 0})
            role_rows += (
                f'<tr style="background:#F8F6F2;">'
                f'<td style="text-align:right;">'
                f'  <span style="font-weight:700;color:#1C1917;">{_e(ra["rank"])}</span>'
                f'  <div style="font-size:11px;color:#A8968F;">{ra["matches"]}场 · {win_rate_text(ra["win_rate"])}胜率</div>'
                f'</td>'
                f'<td style="text-align:center;padding:10px 16px;">'
                f'  <span style="font-size:11px;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:.08em;color:{color};">{label}</span>'
                f'</td>'
                f'<td>'
                f'  <span style="font-weight:700;color:#1C1917;">{_e(rb["rank"])}</span>'
                f'  <div style="font-size:11px;color:#A8968F;">{rb["matches"]}场 · {win_rate_text(rb["win_rate"])}胜率</div>'
                f'</td>'
                f'</tr>'
            )

        overall_rows = (
            stat_row("总场次", a["matches"], b["matches"])
            + stat_row("总胜率", f'{a["win_rate"]}%', f'{b["win_rate"]}%')
            + stat_row("K/D/A", _e(a["kda"]), _e(b["kda"]))
            + stat_row("均伤害", f'{a["avg_damage"]:,}', f'{b["avg_damage"]:,}')
            + stat_row("均治疗", f'{a["avg_heal"]:,}', f'{b["avg_heal"]:,}')
        )

        name_a = _e(a["name"])
        name_b = _e(b["name"])

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{name_a} vs {name_b}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{--bg:#F8F6F2;--surface:#FFFFFF;--border:#E5DDD4;--border-s:#EDE8E1;}}
        * {{margin:0;padding:0;box-sizing:border-box;}}
        body {{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:#1C1917;padding:28px 32px;}}
        .card {{background:var(--surface);border-radius:12px;border:1px solid var(--border);margin-bottom:14px;overflow:hidden;}}
        table {{width:100%;border-collapse:collapse;}}
        td {{padding:11px 14px;font-size:13px;color:#6B5C55;border-bottom:1px solid var(--border-s);}}
        tr:last-child td {{border-bottom:none;}}
    </style>
</head>
<body>
    <div class="card" style="padding:22px 24px;">
        <div style="display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;">
            <div style="display:flex;align-items:center;gap:12px;">
                {icon(a)}
                <div style="font-size:18px;font-weight:700;">{name_a}</div>
            </div>
            <div style="font-size:13px;font-weight:600;color:#A8968F;padding:0 8px;">VS</div>
            <div style="display:flex;align-items:center;gap:12px;justify-content:flex-end;">
                <div style="font-size:18px;font-weight:700;text-align:right;">{name_b}</div>
                {icon(b)}
            </div>
        </div>
    </div>

    <div class="card">
        <div style="padding:12px 14px 8px;background:#F8F6F2;border-bottom:1px solid #EDE8E1;">
            <span style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#A8968F;">职责段位</span>
        </div>
        <table>{role_rows}</table>
    </div>

    <div class="card">
        <div style="padding:12px 14px 8px;background:#F8F6F2;border-bottom:1px solid #EDE8E1;">
            <span style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#A8968F;">综合战绩（竞技）</span>
        </div>
        <table>{overall_rows}</table>
    </div>
</body>
</html>"""

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "compare_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        return output_path
