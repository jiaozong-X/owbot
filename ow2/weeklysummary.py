import html
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .client import OW2Client, url_to_data_uri

sys.stdout.reconfigure(encoding="utf-8")


class WeeklySummaryQuery(OW2Client):
    TZ = timezone(timedelta(hours=8))

    def _kda(self, kills: int, assists: int, deaths: int) -> float:
        return float(kills + assists) if deaths <= 0 else (kills + assists) / deaths

    def _new_bucket(self, name: str = "") -> dict:
        return {
            "name": name,
            "matches": 0,
            "wins": 0,
            "kills": 0,
            "assists": 0,
            "deaths": 0,
            "damage": 0,
            "heal": 0,
            "mitigation": 0,
            "game_time_sec": 0,
        }

    def _add_match(self, bucket: dict, match: dict) -> None:
        bucket["matches"] += 1
        bucket["wins"] += 1 if match.get("matchRet") == 1 else 0
        bucket["kills"] += int(match.get("kill") or 0)
        bucket["assists"] += int(match.get("assist") or 0)
        bucket["deaths"] += int(match.get("death") or 0)
        bucket["damage"] += int(match.get("heroDamage") or 0)
        bucket["heal"] += int(match.get("cure") or 0)
        bucket["mitigation"] += int(match.get("resistDamage") or 0)
        bucket["game_time_sec"] += int(match.get("gameTimeSec") or 0)

    def _finish_bucket(self, bucket: dict) -> dict:
        matches = max(1, bucket["matches"])
        bucket["losses"] = bucket["matches"] - bucket["wins"]
        bucket["win_rate"] = bucket["wins"] / matches * 100
        bucket["kda"] = self._kda(bucket["kills"], bucket["assists"], bucket["deaths"])
        bucket["avg_damage"] = bucket["damage"] / matches
        bucket["avg_heal"] = bucket["heal"] / matches
        return bucket

    def _fmt_duration(self, seconds: int) -> str:
        hours, rem = divmod(max(0, int(seconds)), 3600)
        minutes = rem // 60
        return f"{hours}小时{minutes:02d}分钟" if hours else f"{minutes}分钟"

    def _match_time(self, match: dict) -> datetime:
        return datetime.fromtimestamp(int(match.get("beginTs") or 0) / 1000, self.TZ)

    def _range_for_period(self, period: str, latest_match: dict | None = None) -> tuple[datetime, datetime, str]:
        now = datetime.now(self.TZ)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        this_week_start = today - timedelta(days=today.weekday())
        if period == "today":
            return today, today + timedelta(days=1), "今日总结"
        if period == "yesterday":
            start = today - timedelta(days=1)
            return start, start + timedelta(days=1), "昨日总结"
        if period == "last_week":
            start = this_week_start - timedelta(days=7)
            return start, this_week_start, "上周总结"
        if latest_match:
            latest_day = self._match_time(latest_match).replace(hour=0, minute=0, second=0, microsecond=0)
            start = latest_day - timedelta(days=latest_day.weekday())
        else:
            start = this_week_start
        return start, start + timedelta(days=7), "自然周周报"

    def _fetch_period_matches(self, customer_token: str, game_mode: str, period: str, max_pages: int) -> tuple[list[dict], int, datetime, datetime, str]:
        first_page = self.get_match_list(customer_token, game_mode=game_mode, page=1, max_pages=1)
        start, end, period_name = self._range_for_period(period, first_page[0] if first_page else None)
        matches = []
        pages_used = 0

        for page in range(1, max_pages + 1):
            page_items = first_page if page == 1 else self.get_match_list(customer_token, game_mode=game_mode, page=page, max_pages=1)
            if not page_items:
                break
            pages_used = page
            older_than_range = False
            for match in page_items:
                match_time = self._match_time(match)
                if start <= match_time < end:
                    matches.append(match)
                elif match_time < start:
                    older_than_range = True
            if older_than_range or len(page_items) < 12:
                break

        matches.sort(key=lambda item: item.get("beginTs") or 0, reverse=True)
        return matches, pages_used, start, end, period_name

    def _fetch_match_details(self, matches: list[dict], customer_token: str) -> dict[str, dict]:
        details: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self.get_match_info, match.get("matchId"), customer_token): match
                for match in matches
                if match.get("matchId")
            }
            for future in as_completed(futures):
                match = futures[future]
                try:
                    details[match["matchId"]] = future.result() or {}
                except Exception:
                    details[match["matchId"]] = {}
        return details

    def _longest_streaks(self, matches: list[dict]) -> dict:
        longest_win = longest_loss = current_win = current_loss = 0
        for match in sorted(matches, key=lambda item: item.get("beginTs") or 0):
            if match.get("matchRet") == 1:
                current_win += 1
                current_loss = 0
            else:
                current_loss += 1
                current_win = 0
            longest_win = max(longest_win, current_win)
            longest_loss = max(longest_loss, current_loss)
        return {"win": longest_win, "loss": longest_loss}

    def _attach_teammate_player_icons(self, teammates: list[dict], limit: int = 5) -> None:
        for teammate in teammates[:limit]:
            try:
                player_info = self.search_player(teammate.get("name", ""))
            except Exception:
                player_info = None
            icon = (player_info or {}).get("icon", "")
            teammate["icon"] = url_to_data_uri(icon) if icon else ""

    def query_weekly_summary(self, player_name: str, game_mode: str = "leisure", max_pages: int = 20, period: str = "last_week") -> dict | None:
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        matches, pages_used, start, end, period_name = self._fetch_period_matches(
            player_info["customerToken"], game_mode, period, max_pages
        )
        details = self._fetch_match_details(matches, player_info["customerToken"]) if matches else {}

        summary = self._new_bucket()
        heroes: dict[str, dict] = {}
        maps: dict[str, dict] = {}
        days: dict[str, dict] = defaultdict(lambda: self._new_bucket())
        day_timeline: dict[str, list[dict]] = defaultdict(list)
        teammates: dict[str, dict] = {}
        highlights = []

        player_bnet_id = str(player_info.get("bnetId") or "")
        player_name_resolved = player_info.get("name") or player_name

        for match in matches:
            detail = details.get(match.get("matchId"), {})
            enriched = dict(match)
            enriched["gameTimeSec"] = detail.get("gameTimeSec", 0)
            self._add_match(summary, enriched)

            hero_guid = str(match.get("heroGuid", ""))
            hero_info = self.hero_config.get(hero_guid, {})
            hero_name = hero_info.get("name") or self.hero_guid_names.get(hero_guid, hero_guid or "未知英雄")
            if hero_guid not in heroes:
                heroes[hero_guid] = self._new_bucket(hero_name)
                heroes[hero_guid]["icon"] = url_to_data_uri(hero_info.get("icon", "")) if hero_info.get("icon") else ""
            self._add_match(heroes[hero_guid], enriched)

            map_name = self.map_names.get(str(match.get("mapGuid", "")), "未知地图")
            maps.setdefault(map_name, self._new_bucket(map_name))
            self._add_match(maps[map_name], enriched)

            day_name = self._match_time(match).strftime("%m-%d")
            days[day_name]["name"] = day_name
            self._add_match(days[day_name], enriched)
            day_timeline[day_name].append({
                "time": self._match_time(match).strftime("%H:%M"),
                "result_class": "win" if match.get("matchRet") == 1 else "loss",
                "result": "胜利" if match.get("matchRet") == 1 else "失败",
                "hero": hero_name,
                "map": map_name,
            })

            for teammate in detail.get("teammateList", []) or []:
                teammate_bnet = str(teammate.get("bnetId") or "")
                teammate_name = teammate.get("name") or "未知队友"
                if teammate_bnet == player_bnet_id or teammate_name == player_name_resolved:
                    continue
                key = teammate_bnet or teammate_name
                teammates.setdefault(key, self._new_bucket(teammate_name))
                teammates[key]["name"] = teammate_name
                teammate_match = dict(teammate)
                teammate_match["matchRet"] = match.get("matchRet")
                self._add_match(teammates[key], teammate_match)

            kills = int(match.get("kill") or 0)
            assists = int(match.get("assist") or 0)
            deaths = int(match.get("death") or 0)
            kda = self._kda(kills, assists, deaths)
            highlights.append({
                "time": self._match_time(match).strftime("%m-%d %H:%M"),
                "result": "胜利" if match.get("matchRet") == 1 else "失败",
                "result_class": "win" if match.get("matchRet") == 1 else "loss",
                "hero": hero_name,
                "hero_icon": heroes[hero_guid].get("icon", ""),
                "map": map_name,
                "kills": kills,
                "assists": assists,
                "deaths": deaths,
                "kda": kda,
                "damage": int(match.get("heroDamage") or 0),
                "heal": int(match.get("cure") or 0),
                "score": kda * 1200 + int(match.get("heroDamage") or 0) + int(match.get("cure") or 0) * 0.8,
            })

        summary = self._finish_bucket(summary)
        current_day = start
        while current_day < end:
            day_name = current_day.strftime("%m-%d")
            if day_name not in days:
                days[day_name]["name"] = day_name
            current_day += timedelta(days=1)

        hero_items = [self._finish_bucket(item) for item in heroes.values()]
        map_items = [self._finish_bucket(item) for item in maps.values()]
        day_items = [self._finish_bucket(item) for item in days.values()]
        teammate_items = [self._finish_bucket(item) for item in teammates.values()]
        hero_items.sort(key=lambda item: (-item["matches"], -item["win_rate"], -item["kda"]))
        map_items.sort(key=lambda item: (-item["matches"], -item["win_rate"], item["name"]))
        day_items.sort(key=lambda item: item["name"])
        teammate_items.sort(key=lambda item: (-item["matches"], -item["win_rate"], -item["kda"]))
        self._attach_teammate_player_icons(teammate_items, limit=5)
        highlights.sort(key=lambda item: (-item["score"], -item["kda"], -item["damage"]))

        best = {
            "kda": max(highlights, key=lambda item: item["kda"], default=None),
            "damage": max(highlights, key=lambda item: item["damage"], default=None),
            "heal": max(highlights, key=lambda item: item["heal"], default=None),
            "streak": self._longest_streaks(matches),
        }

        return {
            "player_name": player_name_resolved,
            "player_icon": url_to_data_uri(player_info.get("icon", "")) if player_info.get("icon") else "",
            "mode_label": "休闲" if game_mode == "leisure" else "竞技",
            "period_label": f"{start.strftime('%m-%d')} - {(end - timedelta(seconds=1)).strftime('%m-%d')}",
            "period_name": period_name,
            "pages_used": pages_used,
            "summary": summary,
            "heroes": hero_items,
            "maps": map_items,
            "days": day_items,
            "day_timeline": dict(sorted(day_timeline.items())),
            "teammates": teammate_items,
            "highlights": highlights[:4],
            "best": best,
        }

    def generate_html_report(self, data: dict, output_path: str | None = None) -> str:
        def _e(value):
            return html.escape(str(value)) if value not in (None, "") else ""

        def _num(value):
            return f"{int(value):,}"

        def _pct(value):
            return f"{value:.1f}%"

        def _fmt_md(value: str) -> str:
            try:
                month, day = [int(part) for part in value.split("-", 1)]
                return f"{month}月{day}日"
            except Exception:
                return value

        def _fmt_range(value: str) -> str:
            if " - " not in value:
                return _fmt_md(value)
            start, end = value.split(" - ", 1)
            return f"{_fmt_md(start)} - {_fmt_md(end)}"

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        day_order = {item["name"]: idx for idx, item in enumerate(data.get("days", []))}
        summary = data["summary"]
        hours_played = summary.get("game_time_sec", 0) / 3600

        top_stats = "".join([
            f'<div><b>{summary["matches"]}</b><span>场次</span></div>',
            f'<div><b>{_pct(summary["win_rate"])}</b><span>胜率</span></div>',
            f'<div><b>{summary["kda"]:.2f}</b><span>KDA</span></div>',
            f'<div><b>{hours_played:.1f}h</b><span>时长</span></div>',
        ])

        hero_rows = []
        max_hero_matches = max([item["matches"] for item in data["heroes"][:5]] or [1])
        for item in data["heroes"][:5]:
            icon = f'<img src="{_e(item.get("icon", ""))}" alt="">' if item.get("icon") else '<i></i>'
            width = item["matches"] / max_hero_matches * 100
            hero_rows.append(
                f'<div class="hero-row">{icon}<div><strong>{_e(item["name"])}</strong>'
                f'<span>{item["kills"]}/{item["deaths"]}/{item["assists"]} · {_pct(item["win_rate"])}</span>'
                f'<em><i style="width:{width:.1f}%"></i></em></div><b>{item["matches"]}</b></div>'
            )

        other_icons = "".join(
            f'<img src="{_e(item["icon"])}" title="{_e(item["name"])}" alt="">'
            for item in data["heroes"][5:17]
            if item.get("icon")
        )

        max_map_matches = max([item["matches"] for item in data["maps"][:10]] or [1])
        map_rows = []
        for item in data["maps"][:10]:
            width = item["matches"] / max_map_matches * 100
            map_rows.append(
                f'<div class="map-row"><div><strong>{_e(item["name"])}</strong>'
                f'<em><i style="width:{width:.1f}%"></i></em></div>'
                f'<b>{item["matches"]}场</b><span>{_pct(item["win_rate"])}</span></div>'
            )

        mate_rows = []
        for item in data["teammates"][:5]:
            icon = f'<img src="{_e(item.get("icon", ""))}" alt="">' if item.get("icon") else '<i></i>'
            mate_rows.append(
                f'<div class="mate-card">{icon}<strong>{_e(item["name"])}</strong>'
                f'<span>{item["matches"]}场 · {_pct(item["win_rate"])}</span>'
                f'<span>KDA {item["kda"]:.2f}</span></div>'
            )

        timeline_days = []
        for day in data.get("days", []):
            day_name = day["name"]
            events = sorted(data.get("day_timeline", {}).get(day_name, []), key=lambda item: item["time"])
            dots = "".join(
                f'<i class="{event["result_class"]}" title="{_e(event["time"])} {event["result"]} · {_e(event["hero"])} · {_e(event["map"])}"></i>'
                for event in events
            )
            weekday = weekday_names[day_order.get(day_name, 0) % 7]
            day_range = f'{_e(events[0]["time"])} - {_e(events[-1]["time"])}' if events else "未进行对局"
            day_summary = f'{day["matches"]}场 · {_pct(day["win_rate"])}' if day["matches"] else "休息日"
            timeline_days.append(
                f'<div class="day-line"><div class="day-head"><strong>{weekday} {_e(_fmt_md(day_name))}</strong>'
                f'<span>{day_summary}</span></div>'
                f'<div class="dots">{dots or "<em>休息</em>"}</div>'
                f'<div class="day-range">{day_range}</div></div>'
            )

        best = data["best"]
        high_stats = "".join([
            f'<div class="spot"><span>最高 KDA</span><b>{best["kda"]["kda"]:.2f}</b><em>{_e(best["kda"]["hero"])}</em></div>' if best.get("kda") else "",
            f'<div class="spot"><span>最高输出</span><b>{_num(best["damage"]["damage"])}</b><em>{_e(best["damage"]["hero"])}</em></div>' if best.get("damage") else "",
            f'<div class="spot"><span>最高治疗</span><b>{_num(best["heal"]["heal"])}</b><em>{_e(best["heal"]["hero"])}</em></div>' if best.get("heal") else "",
            f'<div class="spot"><span>最长连胜</span><b>{best["streak"]["win"]}</b><em>连败 {best["streak"]["loss"]}</em></div>',
        ])

        calendar = "".join(
            f'<div class="cal {("good" if item["win_rate"] >= 60 else "mid" if item["win_rate"] >= 45 else "bad")}">'
            f'<b>{weekday_names[day_order.get(item["name"], 0) % 7]}</b><span>{_e(_fmt_md(item["name"]))}</span>'
            f'<span>{item["matches"]}场</span><em>{_pct(item["win_rate"]) if item["matches"] else "休息"}</em></div>'
            for item in data["days"]
        )

        highlights = []
        for item in data["highlights"]:
            icon = f'<img src="{_e(item.get("hero_icon", ""))}" alt="">' if item.get("hero_icon") else '<i></i>'
            highlights.append(
                f'<div class="highlight">{icon}<strong>{_e(item["hero"])}</strong>'
                f'<span>{_e(_fmt_md(item["time"][:5]))} {item["time"][-5:]} · {_e(item["map"])} · {_e(item["result"])}</span>'
                f'<em>{item["kills"]}/{item["deaths"]}/{item["assists"]} · KDA {item["kda"]:.2f}</em></div>'
            )

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "weeklysummary.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        replacements = {
            "{{PLAYER_NAME}}": _e(data["player_name"]),
            "{{PLAYER_ICON}}": _e(data["player_icon"]),
            "{{PERIOD_NAME}}": _e(data["period_name"]),
            "{{PERIOD_LABEL}}": _e(_fmt_range(data["period_label"])),
            "{{MODE_LABEL}}": _e(data["mode_label"]),
            "{{PAGES_USED}}": str(data["pages_used"]),
            "{{TOP_STATS}}": top_stats,
            "{{SIGNATURE}}": "摘要展示重点数据，完整场次仍参与统计。",
            "{{COMMON_HEROES}}": "".join(hero_rows) or '<div class="empty">暂无英雄数据</div>',
            "{{OTHER_HEROES}}": other_icons or '<span>无其他英雄</span>',
            "{{MAP_ROWS}}": "".join(map_rows) or '<div class="empty">暂无地图数据</div>',
            "{{TEAMMATE_ROWS}}": "".join(mate_rows) or '<div class="empty">暂无组队数据</div>',
            "{{TIMELINE}}": "".join(timeline_days) or '<div class="empty">暂无时间线数据</div>',
            "{{HIGHLIGHT_STATS}}": high_stats,
            "{{CALENDAR}}": calendar or '<div class="empty">暂无日历数据</div>',
            "{{HIGHLIGHT_MATCHES}}": "".join(highlights) or '<div class="empty">暂无高光数据</div>',
        }
        for key, value in replacements.items():
            html_template = html_template.replace(key, value)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "weekly_summary.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        return output_path
