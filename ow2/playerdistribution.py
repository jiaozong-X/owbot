import html
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .client import OW2Client, AuthExpiredError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


_PROVINCE_NAME_ALIASES = {
    "北京": "北京市",
    "北京市": "北京市",
    "天津": "天津市",
    "天津市": "天津市",
    "上海": "上海市",
    "上海市": "上海市",
    "重庆": "重庆市",
    "重庆市": "重庆市",
    "河北": "河北省",
    "河北省": "河北省",
    "山西": "山西省",
    "山西省": "山西省",
    "辽宁": "辽宁省",
    "辽宁省": "辽宁省",
    "吉林": "吉林省",
    "吉林省": "吉林省",
    "黑龙江": "黑龙江省",
    "黑龙江省": "黑龙江省",
    "江苏": "江苏省",
    "江苏省": "江苏省",
    "浙江": "浙江省",
    "浙江省": "浙江省",
    "安徽": "安徽省",
    "安徽省": "安徽省",
    "福建": "福建省",
    "福建省": "福建省",
    "江西": "江西省",
    "江西省": "江西省",
    "山东": "山东省",
    "山东省": "山东省",
    "河南": "河南省",
    "河南省": "河南省",
    "湖北": "湖北省",
    "湖北省": "湖北省",
    "湖南": "湖南省",
    "湖南省": "湖南省",
    "广东": "广东省",
    "广东省": "广东省",
    "海南": "海南省",
    "海南省": "海南省",
    "四川": "四川省",
    "四川省": "四川省",
    "贵州": "贵州省",
    "贵州省": "贵州省",
    "云南": "云南省",
    "云南省": "云南省",
    "陕西": "陕西省",
    "陕西省": "陕西省",
    "甘肃": "甘肃省",
    "甘肃省": "甘肃省",
    "青海": "青海省",
    "青海省": "青海省",
    "台湾": "台湾省",
    "台湾省": "台湾省",
    "内蒙古": "内蒙古自治区",
    "内蒙古自治区": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "广西壮族自治区": "广西壮族自治区",
    "西藏": "西藏自治区",
    "西藏自治区": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "宁夏回族自治区": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
    "新疆维吾尔自治区": "新疆维吾尔自治区",
    "香港": "香港特别行政区",
    "香港特别行政区": "香港特别行政区",
    "澳门": "澳门特别行政区",
    "澳门特别行政区": "澳门特别行政区",
}


class PlayerDistributionQuery(OW2Client):
    def normalize_province_name(self, province: str | None) -> str:
        text = str(province or "").strip()
        if not text:
            return ""
        return _PROVINCE_NAME_ALIASES.get(text, text)

    def _extract_province(self, raw: dict) -> str:
        for key in ("sportOpenHeroBillboardList", "sportPresetHeroBillboardList"):
            for item in raw.get(key, []) or []:
                for field in ("province", "provinceName", "area", "region", "location"):
                    province = self.normalize_province_name(item.get(field))
                    if province:
                        return province
        return ""

    def _fetch_one_player(self, user_id: str, player_name: str) -> dict:
        try:
            player_info = self.search_player(player_name)
            if not player_info:
                return {
                    "status": "missing_player",
                    "user_id": str(user_id),
                    "player_name": player_name,
                    "display_name": player_name,
                    "province": "",
                }

            display_name = player_info.get("name") or player_name
            bnet_id = player_info.get("bnetId")
            role_id = player_info.get("roleId")
            if not bnet_id:
                return {
                    "status": "missing_bnet_id",
                    "user_id": str(user_id),
                    "player_name": player_name,
                    "display_name": display_name,
                    "province": "",
                }

            raw = self.get_hero_billboard(
                int(bnet_id),
                role_id,
                player_info.get("billboardToken"),
                player_info.get("billboardDts"),
                player_info.get("billboardServer"),
            )
            province = self._extract_province(raw)
            if not province:
                return {
                    "status": "missing_province",
                    "user_id": str(user_id),
                    "player_name": player_name,
                    "display_name": display_name,
                    "province": "",
                }

            return {
                "status": "ok",
                "user_id": str(user_id),
                "player_name": player_name,
                "display_name": display_name,
                "province": province,
            }
        except AuthExpiredError:
            raise
        except Exception:
            return {
                "status": "request_error",
                "user_id": str(user_id),
                "player_name": player_name,
                "display_name": player_name,
                "province": "",
            }

    def query_distribution(self, bindings: dict, title: str = "玩家分布") -> dict | None:
        if not bindings:
            return None
        if not self._auth.get("billboard_token", ""):
            return {"error": "missing_billboard_token"}

        results = []

        with ThreadPoolExecutor(max_workers=min(5, max(1, len(bindings)))) as executor:
            futures = {
                executor.submit(self._fetch_one_player, str(user_id), player_name): str(user_id)
                for user_id, player_name in bindings.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        provinces: dict[str, list[dict]] = {}
        unresolved: list[dict] = []

        for item in results:
            if item.get("status") != "ok":
                unresolved.append(item)
                continue
            provinces.setdefault(item["province"], []).append(item)

        province_items = []
        for province_name, players in provinces.items():
            sorted_players = sorted(players, key=lambda player: player["display_name"].lower())
            province_items.append(
                {
                    "name": province_name,
                    "count": len(sorted_players),
                    "players": [
                        {
                            "user_id": player["user_id"],
                            "player_name": player["display_name"],
                        }
                        for player in sorted_players
                    ],
                }
            )

        province_items.sort(key=lambda item: (-item["count"], item["name"]))
        max_count = max((item["count"] for item in province_items), default=0)

        province_counts = {item["name"]: item["count"] for item in province_items}
        top_province = province_items[0]["name"] if province_items else ""
        top_count = province_items[0]["count"] if province_items else 0

        return {
            "title": title,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_bound": len(bindings),
            "resolved_count": sum(item["count"] for item in province_items),
            "unresolved_count": len(unresolved),
            "province_items": province_items,
            "province_counts": province_counts,
            "unresolved_players": sorted(unresolved, key=lambda item: item["display_name"].lower()),
            "max_count": max_count,
            "top_province": top_province,
            "top_count": top_count,
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "reports", "player_distribution_report.html")

        def html_text(value) -> str:
            return html.escape(str(value)).encode("ascii", "xmlcharrefreplace").decode("ascii")

        reports_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
        assets_dir = os.path.join(reports_dir, "leaflet_assets")
        output_dir = os.path.dirname(os.path.abspath(output_path))
        leaflet_css = os.path.relpath(os.path.join(assets_dir, "leaflet.css"), output_dir).replace("\\", "/")
        leaflet_js = os.path.relpath(os.path.join(assets_dir, "leaflet.js"), output_dir).replace("\\", "/")
        china_geojson_js = os.path.relpath(os.path.join(assets_dir, "china_100000_full.js"), output_dir).replace("\\", "/")

        province_items = data.get("province_items", [])
        max_count = data.get("max_count", 0)

        top_rows = []
        for index, item in enumerate(province_items[:10], 1):
            top_rows.append(
                '<div class="province-row" data-province="{name}">'
                '<span class="province-rank">#{index}</span>'
                '<span class="province-name">{label}</span>'
                '<span class="province-count">{count}人</span>'
                "</div>".format(
                    index=index,
                    name=html_text(item["name"]),
                    label=html_text(item["name"]),
                    count=item["count"],
                )
            )
        top_rows_html = "".join(top_rows) or '<div class="empty-state">暂无可显示的省份数据</div>'

        html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_text(data.get("title", "玩家分布"))}</title>
    <link rel="stylesheet" href="{leaflet_css}">
    <style>
        :root {{
            --bg: #f4ede3;
            --panel: rgba(255, 255, 255, 0.94);
            --panel-border: rgba(77, 63, 49, 0.12);
            --text-1: #2e261f;
            --text-2: #6b5c4f;
            --text-3: #9c8e82;
            --accent: #d46f3d;
            --accent-dark: #a64e22;
            --map-bg: #eee4d8;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        html, body {{
            width: 1600px;
            font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
            color: var(--text-1);
            background: #F8F6F2;
            padding: 0;
            margin: 0;
        }}

        .app {{
            display: flex;
            flex-direction: column;
            width: 1600px;
            padding: 16px;
            gap: 14px;
        }}

        .header-bar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--panel);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            box-shadow: 0 8px 24px rgba(74, 58, 43, 0.08);
            padding: 16px 24px;
        }}

        .header-left {{
            display: flex;
            align-items: center;
            gap: 20px;
        }}

        .title-block {{
            display: flex;
            flex-direction: column;
        }}

        .eyebrow {{
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--accent);
        }}

        h1 {{
            margin-top: 4px;
            font-size: 20px;
            line-height: 1.2;
        }}

        .stat-row {{
            display: flex;
            gap: 32px;
        }}

        .stat-item {{
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 8px 16px;
        }}

        .stat-value {{
            font-size: 26px;
            font-weight: 800;
            color: var(--text-1);
            line-height: 1;
        }}

        .stat-label {{
            margin-top: 4px;
            font-size: 11px;
            color: var(--text-3);
        }}

        .main-content {{
            display: grid;
            grid-template-columns: 1fr 260px;
            gap: 14px;
            height: 800px;
        }}

        .map-shell {{
            position: relative;
            border-radius: 20px;
            border: 1px solid var(--panel-border);
            box-shadow: 0 16px 48px rgba(74, 58, 43, 0.10);
            background: linear-gradient(180deg, #f4ede3 0%, var(--map-bg) 100%);
            overflow: hidden;
        }}

        #map {{
            width: 100%;
            height: 100%;
        }}

        .leaflet-container {{ background: transparent; }}

        .province-label {{
            color: #5a4a3a;
            font-size: 12px;
            font-weight: 700;
            text-shadow: 0 1px 2px rgba(255,255,255,0.95), 0 0 10px rgba(255,255,255,0.70);
            white-space: nowrap;
            pointer-events: none;
        }}

        .leaflet-tooltip {{
            font-size: 13px !important;
            padding: 8px 12px !important;
            border-radius: 8px !important;
            box-shadow: 0 6px 16px rgba(0,0,0,0.12) !important;
        }}

        .legend {{
            position: absolute;
            right: 16px;
            bottom: 16px;
            z-index: 500;
            padding: 12px 14px;
            border-radius: 12px;
            background: rgba(255,255,255,0.95);
            border: 1px solid rgba(77,63,49,0.10);
            box-shadow: 0 8px 24px rgba(74, 58, 43, 0.10);
        }}

        .legend-title {{
            font-size: 12px;
            font-weight: 700;
            color: var(--text-2);
            margin-bottom: 8px;
        }}

        .legend-scale {{
            display: flex;
            gap: 6px;
            margin-bottom: 6px;
        }}

        .legend-box {{
            width: 24px;
            height: 14px;
            border-radius: 4px;
            border: 1px solid rgba(77, 63, 49, 0.08);
        }}

        .legend-labels {{
            display: flex;
            justify-content: space-between;
            font-size: 11px;
            color: var(--text-3);
        }}

        .sidebar {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .panel {{
            background: var(--panel);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            box-shadow: 0 12px 36px rgba(74, 58, 43, 0.08);
            padding: 16px 18px;
        }}

        .section-title {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }}

        .section-title strong {{
            font-size: 14px;
            font-weight: 700;
        }}

        .section-title span {{
            font-size: 11px;
            color: var(--text-3);
        }}

        .province-list {{
            display: grid;
            gap: 5px;
        }}

        .province-row {{
            display: grid;
            grid-template-columns: 32px 1fr auto;
            align-items: center;
            gap: 8px;
            padding: 7px 10px;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.65);
            border: 1px solid rgba(77, 63, 49, 0.06);
        }}

        .province-rank {{
            font-size: 12px;
            font-weight: 800;
            color: var(--accent-dark);
        }}

        .province-name {{
            font-size: 12px;
            font-weight: 600;
        }}

        .province-count {{
            font-size: 11px;
            font-weight: 700;
            color: var(--accent-dark);
        }}
    </style>
</head>
<body>
    <div class="app">
        <header class="header-bar">
            <div class="header-left">
                <div class="title-block">
                    <div class="eyebrow">玩家分布</div>
                    <h1>{html_text(data.get("title", "玩家分布"))}</h1>
                </div>
            </div>
            <div class="stat-row">
                <div class="stat-item">
                    <div class="stat-value">{data.get("total_bound", 0)}</div>
                    <div class="stat-label">绑定玩家</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{data.get("resolved_count", 0)}</div>
                    <div class="stat-label">已识别</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{len(province_items)}</div>
                    <div class="stat-label">覆盖省份</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">{data.get("unresolved_count", 0)}</div>
                    <div class="stat-label">未识别</div>
                </div>
            </div>
        </header>

        <div class="main-content">
            <main class="map-shell">
                <div id="map"></div>
                <div class="legend">
                    <div class="legend-title">玩家分布密度</div>
                    <div class="legend-scale">
                        <span class="legend-box" style="background:#efe6da;"></span>
                        <span class="legend-box" style="background:#f1c8a7;"></span>
                        <span class="legend-box" style="background:#e7a06f;"></span>
                        <span class="legend-box" style="background:#d77948;"></span>
                        <span class="legend-box" style="background:#b55324;"></span>
                    </div>
                    <div class="legend-labels">
                        <span>0</span>
                        <span>{max(data.get("max_count", 0), 1)}</span>
                    </div>
                </div>
            </main>

            <aside class="sidebar">
                <section class="panel" style="flex:1; overflow:hidden; display:flex; flex-direction:column;">
                    <div class="section-title">
                        <strong>省份热度</strong>
                        <span>Top 10</span>
                    </div>
                    <div class="province-list" style="flex:1; overflow-y:auto;">{top_rows_html}</div>
                </section>
            </aside>
        </div>
        <div style="text-align:center;padding:10px 0 6px;font-size:11px;color:#A8968F;background:#FFFFFF;margin-top:10px;border-radius:8px;">守望先锋 2 · 玩家分布统计 · 机器人反馈群 412275154</div>
    </div>

    <script src="{leaflet_js}"></script>
    <script src="{china_geojson_js}"></script>
    <script>
        const provinceCounts = {json.dumps(data.get("province_counts", {}), ensure_ascii=True)};
        const maxCount = Math.max({data.get("max_count", 0)}, 1);

        function colorForCount(count) {{
            if (!count) return "#efe6da";
            const ratio = count / maxCount;
            if (ratio >= 0.8) return "#b55324";
            if (ratio >= 0.55) return "#d77948";
            if (ratio >= 0.3) return "#e7a06f";
            return "#f1c8a7";
        }}

        function baseStyle(feature) {{
            const name = feature?.properties?.name || "";
            const count = provinceCounts[name] || 0;
            return {{
                color: "#ffffff",
                weight: 1,
                opacity: 0.9,
                fillColor: colorForCount(count),
                fillOpacity: count > 0 ? 0.9 : 0.65
            }};
        }}

        function hoverStyle(feature) {{
            const name = feature?.properties?.name || "";
            const count = provinceCounts[name] || 0;
            return {{
                color: "#7A3D1A",
                weight: 2,
                opacity: 1,
                fillColor: colorForCount(count),
                fillOpacity: 1
            }};
        }}

        const rawFeatures = (window.CHINA_GEOJSON?.features || []).filter((f) => f?.properties?.name && f?.properties?.level === "province");
        const chinaGeoJSON = {{ type: "FeatureCollection", features: rawFeatures }};

        const map = L.map("map", {{
            zoomControl: false,
            attributionControl: false,
            minZoom: 3.5,
            maxZoom: 7,
            zoomSnap: 0.25
        }});

        const geoLayer = L.geoJSON(chinaGeoJSON, {{
            style: baseStyle,
            onEachFeature(feature, layer) {{
                const name = feature.properties?.name || "";
                const count = provinceCounts[name] || 0;
                layer.bindTooltip(name + "：" + count + "人", {{ sticky: true, direction: "top", opacity: 0.92 }});
                layer.on("mouseover", () => layer.setStyle(hoverStyle(feature)));
                layer.on("mouseout", () => geoLayer.resetStyle(layer));
            }}
        }}).addTo(map);

        const bounds = geoLayer.getBounds();
        map.fitBounds(bounds.pad(0.02));
        map.setMaxBounds(bounds.pad(0.15));

        const labelLayer = L.layerGroup();
        rawFeatures.forEach((feature) => {{
            const props = feature.properties || {{}};
            const center = Array.isArray(props.centroid) ? props.centroid : props.center;
            if (!Array.isArray(center) || center.length < 2) return;
            const name = props.name || "";
            const count = provinceCounts[name] || 0;
            const label = count > 0 ? name + " " + count : name;
            labelLayer.addLayer(L.marker([center[1], center[0]], {{
                interactive: false,
                icon: L.divIcon({{ className: "province-label", html: label, iconSize: [0, 0] }})
            }}));
        }});
        labelLayer.addTo(map);
    </script>
</body>
</html>
"""

        with open(output_path, "w", encoding="utf-8-sig") as file:
            file.write(html_doc)
        return output_path
