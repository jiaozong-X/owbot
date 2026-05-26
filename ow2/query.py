import html
import os
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .client import OW2Client, url_to_data_uri

sys.stdout.reconfigure(encoding="utf-8")


class LatestMatchQuery(OW2Client):
    def __init__(self):
        super().__init__()
        self.perk_guid_aliases = {
            "920986123797276873": "920986123797276128",
            "920986123797276869": "920986123797276264",
        }

        # 完整的英雄GUID到职责的映射表（0=坦克, 1=输出, 2=支援）
        self.hero_role_map = {
            # 坦克
            "207165582859042880": 0,  # D.Va
            "207165582859042920": 0,  # 末日铁拳
            "207165582859043387": 0,  # 狂鼠（实际上是破坏球）
            "207165582859042938": 0,  # 奥丽莎
            "207165582859043119": 0,  # 拉玛刹（实际上是拉玛刹）
            "207165582859042837": 0,  # 莱因哈特
            "207165582859043627": 0,  # 路霸
            "207165582859042882": 0,  # 西格玛
            "207165582859043388": 0,  # 温斯顿
            "207165582859043389": 0,  # 破坏球
            "207165582859043390": 0,  # 扎莉娅
            "207165582859044118": 0,  # 玛伽
            "207165582859044119": 0,  # 拉玛刹

            # 输出
            "207165582859042838": 1,  # 艾什
            "207165582859042839": 1,  # 堡垒
            "207165582859042840": 1,  # 卡西迪
            "207165582859042841": 1,  # 回声
            "207165582859042842": 1,  # 源氏
            "207165582859042843": 1,  # 半藏
            "207165582859042844": 1,  # 狂鼠
            "207165582859042845": 1,  # 法老之鹰
            "207165582859042846": 1,  # 死神
            "207165582859042847": 1,  # 士兵76
            "207165582859042848": 1,  # 索杰恩
            "207165582859042849": 1,  # 黑影
            "207165582859042850": 1,  # 对称
            "207165582859042851": 1,  # 托比昂
            "207165582859042852": 1,  # 猎空
            "207165582859042853": 1,  # 黑百合
            "207165582859043036": 1,  # 文图拉

            # 支援
            "207165582859043131": 2,  # 安娜
            "207165582859043132": 2,  # 巴蒂斯特
            "207165582859043133": 2,  # 布丽吉塔
            "207165582859043361": 2,  # 伊利丹（实际上是伊利丹）
            "207165582859044036": 2,  # 基里科
            "207165582859044067": 2,  # 生命之织者
            "207165582859043362": 2,  # 卢西奥
            "207165582859043363": 2,  # 天使
            "207165582859043364": 2,  # 莫伊拉
            "207165582859043365": 2,  # 朱诺
            "207165582859043366": 2,  # 禅雅塔
        }

        self.hero_names = {
            "ana": "安娜", "ashe": "艾什", "baptiste": "巴蒂斯特", "bastion": "堡垒",
            "brigitte": "布丽吉塔", "cassidy": "卡西迪", "dva": "D.Va", "doomfist": "末日铁拳",
            "echo": "回声", "genji": "源氏", "hanzo": "半藏", "illari": "伊拉锐",
            "junker-queen": "渣客女王", "junkrat": "狂鼠", "juno": "朱诺", "kiriko": "雾子",
            "lifeweaver": "生命之织", "lucio": "卢西奥", "mauga": "毛加", "mei": "美",
            "mercy": "天使", "moira": "莫伊拉", "orisa": "奥丽莎", "pharah": "法老之鹰",
            "ramattra": "拉玛刹", "reaper": "死神", "reinhardt": "莱因哈特", "roadhog": "路霸",
            "sigma": "西格玛", "sojourn": "索杰恩", "soldier-76": "士兵76", "sombra": "黑影",
            "symmetra": "秩序之光", "torbjorn": "托比昂", "tracer": "猎空", "venture": "探险",
            "widowmaker": "黑百合", "winston": "温斯顿", "wrecking-ball": "破坏球",
            "zarya": "查莉娅", "zenyatta": "禅雅塔",
        }

        self.hero_guid_names.update({
            "207165582859044067": "朱诺",
            "207165582859044056": "探险",
            "207165582859044036": "毛加",
            "207165582859043971": "探险",
            "207165582859043875": "毛加",
            "207165582859043779": "伊拉锐",
            "207165582859043683": "生命之织",
            "207165582859043587": "拉玛刹",
            "207165582859043491": "渣客女王",
            "207165582859043395": "索杰恩",
            "207165582859043299": "雾子",
            "207165582859043203": "回声",
            "207165582859043107": "巴蒂斯特",
            "207165582859043011": "艾什",
            "207165582859042915": "破坏球",
            "207165582859042857": "布丽吉塔",
            "207165582859042848": "布丽吉塔",
            "207165582859042819": "布丽吉塔",
            "207165582859042723": "莫伊拉",
            "207165582859042627": "末日铁拳",
            "207165582859042531": "奥丽莎",
            "207165582859042435": "安娜",
            "207165582859042339": "黑影",
            "207165582859042243": "源氏",
            "207165582859042147": "猎空",
            "207165582859042051": "半藏",
            "207165582859041955": "狂鼠",
            "207165582859041859": "美",
            "207165582859041763": "托比昂",
            "207165582859041667": "黑百合",
            "207165582859041571": "法老之鹰",
            "207165582859041475": "死神",
            "207165582859041379": "士兵76",
            "207165582859041283": "秩序之光",
            "207165582859041187": "卡西迪",
            "207165582859041091": "堡垒",
            "207165582859040995": "路霸",
            "207165582859040899": "温斯顿",
            "207165582859040803": "查莉娅",
            "207165582859040707": "莱因哈特",
            "207165582859040611": "西格玛",
            "207165582859040515": "D.Va",
            "207165582859040419": "禅雅塔",
            "207165582859040323": "卢西奥",
            "207165582859040227": "天使",
        })

        # 英雄名 → 职责排序权重（0=坦克 1=输出 2=支援）
        _tank_names = {
            "D.Va", "西格玛", "莱因哈特", "查莉娅", "温斯顿", "路霸",
            "奥丽莎", "末日铁拳", "破坏球", "渣客女王", "拉玛刹", "毛加", "危险", "哈扎德",
        }
        _dps_names = {
            "堡垒", "卡西迪", "秩序之光", "士兵76", "士兵：76", "死神", "法老之鹰",
            "黑百合", "托比昂", "美", "狂鼠", "半藏", "猎空", "源氏",
            "黑影", "艾什", "回声", "索杰恩", "探险", "弗蕾娅", "斩仇", "埃姆雷",
        }
        _support_names = {
            "天使", "卢西奥", "禅雅塔", "安娜", "莫伊拉", "布丽吉塔",
            "巴蒂斯特", "雾子", "柊雾子", "生命之织", "无漾", "伊拉锐", "朱诺",
        }
        self.hero_role_by_name: dict[str, int] = {}
        for n in _tank_names:    self.hero_role_by_name[n] = 0
        for n in _dps_names:     self.hero_role_by_name[n] = 1
        for n in _support_names: self.hero_role_by_name[n] = 2

    def format_hero_name(self, hero_key_or_guid):
        """格式化英雄名称（优先使用 hero_config，这是最准确、最权威的来源）"""
        if not hero_key_or_guid:
            return "未知英雄"
        hero_guid = str(hero_key_or_guid)
        # 优先使用 hero_config 中的英雄名字（最准确、最权威）
        hero_info = self.hero_config.get(hero_guid)
        if hero_info and hero_info.get("name"):
            return hero_info["name"]
        # 备用：使用 hero_guid_names
        hero_name = self.hero_guid_names.get(hero_guid)
        if hero_name:
            return hero_name
        # 备用：使用 hero_names
        hero_name = self.hero_names.get(hero_key_or_guid)
        if hero_name:
            return hero_name
        return "未知英雄"

    def _hero_guid_matches(self, left_guid, right_guid) -> bool:
        """兼容新旧 heroGuid：优先直接比对，失败时回退到候选英雄名集合比对。"""
        left = str(left_guid or "")
        right = str(right_guid or "")
        if not left or not right:
            return False
        if left == right:
            return True
        left_names = self._hero_name_candidates(left)
        right_names = self._hero_name_candidates(right)
        if not left_names or not right_names:
            return False
        return bool(left_names & right_names)

    def _hero_name_candidates(self, hero_guid) -> set[str]:
        names: set[str] = set()
        guid = str(hero_guid or "")
        if not guid:
            return names
        hero_info = self.hero_config.get(guid)
        if hero_info:
            name = str(hero_info.get("name", "") or "").strip()
            if name:
                names.add(name)
        fallback_name = str(self.hero_guid_names.get(guid, "") or "").strip()
        if fallback_name:
            names.add(fallback_name)
        return names

    def _resolve_player_perks(self, player: dict) -> list[dict]:
        hero_guid = str(player.get("heroGuid", ""))
        resolved = []
        for perk in player.get("perks", []) or []:
            perk_guid = str(perk.get("guid", ""))
            perk_level = int(perk.get("perkLevel", 0) or 0)
            perk_info = self.perk_config.get(perk_guid, {})
            if not perk_info:
                alias_guid = self.perk_guid_aliases.get(perk_guid, "")
                if alias_guid:
                    perk_info = self.perk_config.get(alias_guid, {})
            perk_hero_guid = str(perk_info.get("heroGuid", ""))
            # The perk config currently uses a different hero id namespace from
            # match payloads for some heroes, so a strict heroGuid match hides
            # valid perks even when the perk guid resolves correctly.
            if perk_info:
                resolved.append(
                    {
                        "level": perk_level,
                        "name": perk_info.get("name", ""),
                        "description": perk_info.get("description", ""),
                        "icon": perk_info.get("icon", ""),
                        "heroGuidMatched": self._hero_guid_matches(perk_hero_guid, hero_guid),
                    }
                )
            else:
                resolved.append(
                    {
                        "level": perk_level,
                        "name": f"威能 {perk_level}" if perk_level else "未知威能",
                        "description": "",
                        "icon": "",
                    }
                )
        resolved.sort(key=lambda item: item.get("level", 0))
        return resolved

    def _role_sort_key(self, player: dict) -> int:
        """返回职责排序权重：0=tank 1=dps 2=healer，未知排最后"""
        hero_guid = str(player.get("heroGuid", ""))
        # 优先使用 hero_config 中的 roleType
        hero_info = self.hero_config.get(hero_guid)
        if hero_info:
            role_type = hero_info.get("roleType", "")
            if role_type == "tank":
                return 0
            elif role_type == "dps":
                return 1
            elif role_type == "healer":
                return 2
        # 备用：使用本地映射表查找职责
        role = self.hero_role_map.get(hero_guid)
        if role is not None:
            return role
        # 如果都没有，返回9（排到最后）
        return 9

    def query_nth_match(self, player_name, n=1, game_mode="leisure"):
        """查询玩家第 n 场对局（1-based）"""
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        customer_token = player_info["customerToken"]
        matches = self.get_match_list(customer_token, game_mode=game_mode)
        if not matches:
            return None

        if n < 1 or n > len(matches):
            return None

        target_match = matches[n - 1]
        match_id = target_match.get("matchId")

        match_data = self.get_match_info(match_id, customer_token)
        if not match_data:
            return None

        return self._parse_match(player_info, match_id, match_data, game_mode=game_mode)

    def query_latest_match(self, player_name, game_mode="leisure"):
        """查询玩家最新对局（等同于 query_nth_match(player_name, 1)）"""
        return self.query_nth_match(player_name, 1, game_mode=game_mode)

    def _parse_match(self, player_info, match_id, match_data, game_mode="leisure"):
        """解析对局数据并复用 countInfo 缓存，避免重复段位请求。"""
        map_guid = str(match_data.get("mapGuid", ""))
        map_name = self.map_names.get(map_guid, "未知地图")
        result = "胜利" if match_data.get("matchRet", 0) == 1 else "失败"
        start_time = match_data.get("startTime", 0)

        game_time_sec = match_data.get("gameTimeSec", 0)
        if game_time_sec > 0:
            minutes = int(game_time_sec // 60)
            seconds = int(game_time_sec % 60)
            game_duration = f"{minutes}:{seconds:02d}"
        else:
            game_duration = "—"

        banned_heroes = {"team": [], "enemy": []}
        try:
            hero_icon_map = {
                guid: hero_info["icon"]
                for guid, hero_info in self.hero_config.items()
                if hero_info.get("icon")
            }
            for player in match_data.get("teammateList", []) + match_data.get("enemyList", []):
                guid = str(player.get("heroGuid", ""))
                icon = player.get("heroIcon", "")
                if guid and icon and guid not in hero_icon_map:
                    hero_icon_map[guid] = icon
            for side, guids in (
                ("team", match_data.get("teamBanHeroGuids", [])),
                ("enemy", match_data.get("enemyBanHeroGuids", [])),
            ):
                for guid in guids:
                    hero_guid = str(guid)
                    hero_name = self.format_hero_name(hero_guid)
                    if hero_name == "未知英雄":
                        continue
                    hero_icon = self.hero_config.get(hero_guid, {}).get("icon") or hero_icon_map.get(hero_guid, "")
                    banned_heroes[side].append({"name": hero_name, "icon": hero_icon})
        except Exception:
            pass

        if start_time > 10000000000:
            match_time = datetime.fromtimestamp(start_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
        elif start_time > 0:
            match_time = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
        else:
            match_time = "未知"

        teammates = match_data.get("teammateList", [])
        enemies = match_data.get("enemyList", [])
        all_players = teammates + enemies
        player_count_info = self._fetch_player_count_info(all_players, game_mode="sport")
        team_kdas, team_damage, team_heal = self._prepare_players(teammates, player_count_info)
        enemy_kdas, enemy_damage, enemy_heal = self._prepare_players(enemies, player_count_info)

        return {
            "player_name": player_info["name"],
            "match_id": match_id,
            "map_name": map_name,
            "result": result,
            "match_time": match_time,
            "game_duration": game_duration,
            "teammates": teammates,
            "enemies": enemies,
            "team_avg_kda": sum(team_kdas) / len(team_kdas) if team_kdas else 0,
            "enemy_avg_kda": sum(enemy_kdas) / len(enemy_kdas) if enemy_kdas else 0,
            "team_damage": team_damage,
            "team_heal": team_heal,
            "enemy_damage": enemy_damage,
            "enemy_heal": enemy_heal,
            "team_rank_range": self._fetch_rank_range(teammates, player_count_info),
            "enemy_rank_range": self._fetch_rank_range(enemies, player_count_info),
            "banned_heroes": banned_heroes,
        }

    def _player_role_type(self, player: dict):
        hero_guid = str(player.get("heroGuid", ""))
        hero_info = self.hero_config.get(hero_guid)
        return hero_info.get("roleType") if hero_info else None

    def _fetch_player_count_info(self, players: list, game_mode: str = "sport") -> dict:
        tokens = list({p.get("customerToken") for p in players if p.get("customerToken")})
        if not tokens:
            return {}
        results: dict = {}
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(self.get_count_info, token, game_mode=game_mode): token for token in tokens}
            for future in as_completed(futures):
                token = futures[future]
                try:
                    results[token] = future.result() or {}
                except Exception:
                    results[token] = {}
        return results

    def _prepare_players(self, players: list, player_count_info: dict) -> tuple[list, int, int]:
        kdas = []
        total_damage = 0
        total_heal = 0
        for player in players:
            kda = self.calculate_kda(player.get("kill", 0), player.get("assist", 0), player.get("death", 0))
            kdas.append(kda)
            total_damage += player.get("heroDamage", 0)
            total_heal += player.get("cure", 0)
            player["perks_resolved"] = self._resolve_player_perks(player)
            token = player.get("customerToken")
            if token and token in player_count_info:
                rank_info = self._pick_rank_from_count_info(
                    player_count_info.get(token, {}),
                    role_type=self._player_role_type(player),
                )
                player["rank"] = self._fmt_rank(rank_info)
            else:
                player["rank"] = "—"
        return kdas, total_damage, total_heal

    def _fetch_rank_range(self, players: list, player_count_info: dict | None = None) -> str:
        player_count_info = player_count_info or self._fetch_player_count_info(players, game_mode="sport")
        valid = []
        for player in players:
            token = player.get("customerToken")
            if not token:
                continue
            rank_info = self._pick_rank_from_count_info(
                player_count_info.get(token, {}),
                role_type=self._player_role_type(player),
            )
            if rank_info["rankName"] != "None":
                valid.append(rank_info)
        if not valid:
            return "—"

        def fmt(rank_info):
            cn = self.rank_cn.get(rank_info["rankName"], rank_info["rankName"])
            return f"{cn}{rank_info['rankSubTier']}" if rank_info["rankSubTier"] > 0 else cn

        hi = fmt(max(valid, key=lambda item: item["totalScore"]))
        lo = fmt(min(valid, key=lambda item: item["totalScore"]))
        return hi if hi == lo else f"{hi} ~ {lo}"

    def _generate_html_report_legacy(self, match_data, output_path=None):
        """生成现代化 HTML 报告"""
        def _e(s): return html.escape(str(s)) if s else ""  # HTML 转义辅助函数
        
        player_name = match_data["player_name"]
        match_id = match_data["match_id"]
        map_name = match_data["map_name"]
        result = match_data["result"]
        match_time = match_data["match_time"]
        game_duration = match_data.get("game_duration", "—")
        teammates = match_data["teammates"]
        enemies = match_data["enemies"]
        team_avg_kda = match_data["team_avg_kda"]
        enemy_avg_kda = match_data["enemy_avg_kda"]
        team_damage = match_data["team_damage"]
        team_heal = match_data["team_heal"]
        enemy_damage = match_data["enemy_damage"]
        enemy_heal = match_data["enemy_heal"]
        team_rank_range = match_data.get("team_rank_range", "—")
        enemy_rank_range = match_data.get("enemy_rank_range", "—")
        banned_heroes = match_data.get("banned_heroes", [])

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "match.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        result_badge_class = "win-badge" if result == "胜利" else "lose-badge"
        result_border_color = "#2B7A3D" if result == "胜利" else "#B91C1C"

        html_template = html_template.replace("{{PLAYER_NAME}}", _e(player_name))
        html_template = html_template.replace("{{MATCH_ID}}", _e(match_id))
        html_template = html_template.replace("{{MAP_NAME}}", _e(map_name))
        html_template = html_template.replace("{{RESULT}}", _e(result))
        html_template = html_template.replace("{{MATCH_TIME}}", _e(match_time))
        html_template = html_template.replace("{{GAME_DURATION}}", _e(game_duration))
        html_template = html_template.replace("{{TEAM_AVG_KDA}}", f"{team_avg_kda:.2f}")
        html_template = html_template.replace("{{ENEMY_AVG_KDA}}", f"{enemy_avg_kda:.2f}")
        html_template = html_template.replace("{{TEAM_DAMAGE}}", f"{team_damage:,}")
        html_template = html_template.replace("{{TEAM_HEAL}}", f"{team_heal:,}")
        html_template = html_template.replace("{{ENEMY_DAMAGE}}", f"{enemy_damage:,}")
        html_template = html_template.replace("{{ENEMY_HEAL}}", f"{enemy_heal:,}")
        html_template = html_template.replace("{{RESULT_BADGE_CLASS}}", result_badge_class)
        html_template = html_template.replace("{{RESULT_BORDER_COLOR}}", result_border_color)
        html_template = html_template.replace("{{TEAM_RANK_RANGE}}", _e(team_rank_range))
        html_template = html_template.replace("{{ENEMY_RANK_RANGE}}", _e(enemy_rank_range))

        # 生成禁用英雄显示HTML（分别生成我方禁用和敌方禁用）
        team_banned_html = ""
        if banned_heroes["team"]:
            team_avatars = []
            for hero in banned_heroes["team"]:
                hero_icon = url_to_data_uri(hero.get("icon", ""))
                hero_name_esc = _e(hero["name"])
                team_avatars.append(f'<img src="{hero_icon}" alt="{hero_name_esc}" title="{hero_name_esc}" style="width:36px;height:36px;border-radius:6px;border:1.5px solid #FEE2E2;background:#FFF5F5;">')
            team_banned_html = f'<div style="margin-top:6px;"><div class="lbl" style="color:#D4713A;margin-bottom:4px;">我方禁用</div><div style="display:flex;gap:5px;">{"".join(team_avatars)}</div></div>'

        enemy_banned_html = ""
        if banned_heroes["enemy"]:
            enemy_avatars = []
            for hero in banned_heroes["enemy"]:
                hero_icon = url_to_data_uri(hero.get("icon", ""))
                hero_name_esc = _e(hero["name"])
                enemy_avatars.append(f'<img src="{hero_icon}" alt="{hero_name_esc}" title="{hero_name_esc}" style="width:36px;height:36px;border-radius:6px;border:1.5px solid #FEE2E2;background:#FFF5F5;">')
            enemy_banned_html = f'<div style="margin-top:6px;"><div class="lbl" style="color:#B91C1C;margin-bottom:4px;">敌方禁用</div><div style="display:flex;gap:5px;justify-content:flex-end;">{"".join(enemy_avatars)}</div></div>'

        html_template = html_template.replace("{{TEAM_BANNED_HEROES}}", team_banned_html)
        html_template = html_template.replace("{{ENEMY_BANNED_HEROES}}", enemy_banned_html)

        def player_card_html(player, kda_color):
            name = _e(player.get("name", "未知"))
            hero_guid = player.get("heroGuid", "")
            hero_name = _e(self.format_hero_name(hero_guid))
            hero_icon_src = self.hero_config.get(str(hero_guid), {}).get("icon") or player.get("heroIcon", "")
            hero_icon = url_to_data_uri(hero_icon_src)
            kill = player.get("kill", 0)
            death = player.get("death", 0)
            assist = player.get("assist", 0)
            final_hit = player.get("finalHit", 0)
            damage = player.get("heroDamage", 0)
            heal = player.get("cure", 0)
            healing_taken = int(player.get("healingTaken", 0) or 0)
            kda = self.calculate_kda(kill, assist, death)
            rank = _e(player.get("rank", "—"))
            return f"""
                <div class="player-card">
                    <div style="display:flex;align-items:center;gap:12px;">
                        <img src="{hero_icon}" alt="{hero_name}" title="{hero_name}" class="hero-avatar">
                        <div style="flex:1;min-width:0;">
                            <div style="font-size:13px;font-weight:600;color:#1C1917;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>
                            <div style="font-size:11px;color:#A8968F;margin-top:1px;">{hero_name}</div>
                        </div>
                        <div style="display:flex;align-items:center;flex-shrink:0;">
                            <div style="text-align:center;padding:0 12px;border-left:1px solid #EDE8E1;">
                                <div style="font-size:14px;font-weight:600;color:#D4713A;font-variant-numeric:tabular-nums;letter-spacing:-0.02em;line-height:1.1;">{rank}</div>
                                <div style="font-size:9px;color:#A8968F;text-transform:uppercase;letter-spacing:0.06em;margin-top:1px;">段位</div>
                            </div>
                            <div style="text-align:center;padding:0 12px;border-left:1px solid #EDE8E1;">
                                <div style="font-size:19px;font-weight:700;color:{kda_color};font-variant-numeric:tabular-nums;letter-spacing:-0.02em;line-height:1.1;">{kda:.2f}</div>
                                <div style="font-size:9px;color:#A8968F;text-transform:uppercase;letter-spacing:0.06em;margin-top:1px;">KDA</div>
                            </div>
                            <div style="padding:0 0 0 12px;border-left:1px solid #EDE8E1;min-width:132px;">
                                <div style="font-size:11px;color:#6B5C55;margin-bottom:3px;"><span style="color:#A8968F;">K/D/A</span>&ensp;{kill} / {death} / {assist}&emsp;<span style="color:#7C3AED;">◉</span>&ensp;{final_hit}</div>
                                <div style="font-size:11px;color:#6B5C55;margin-bottom:3px;"><span style="color:#D4713A;">⚔</span>&ensp;{damage:,}&emsp;<span style="color:#2F9E44;">✚</span>&ensp;{heal:,}</div>
                                <div style="font-size:11px;color:#6B5C55;"><span style="color:#A8968F;">受治疗</span>&ensp;{healing_taken:,}</div>
                            </div>
                        </div>
                    </div>
                </div>
            """

        # 按职责排序：坦克(0)→输出(1)→支援(2)
        teammates_sorted = sorted(teammates, key=self._role_sort_key)
        enemies_sorted = sorted(enemies, key=self._role_sort_key)

        team_html = "".join(player_card_html(p, "#D4713A") for p in teammates_sorted)
        enemy_html = "".join(player_card_html(p, "#B91C1C") for p in enemies_sorted)

        html_template = html_template.replace("{{TEAM_PLAYERS}}", team_html)
        html_template = html_template.replace("{{ENEMY_PLAYERS}}", enemy_html)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "latest_match_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_template)

        print(f"[成功] 报告已生成: {os.path.basename(output_path)}")


    def _render_banned_html(self, items, title, color, justify):
        if not items:
            return ""
        avatars = []
        for hero in items:
            hero_icon = url_to_data_uri(hero.get("icon", ""))
            hero_name_esc = html.escape(str(hero.get("name", ""))) if hero.get("name") else ""
            avatars.append(
                f'<img src="{hero_icon}" alt="{hero_name_esc}" title="{hero_name_esc}" '
                'style="width:36px;height:36px;border-radius:6px;border:1.5px solid #FEE2E2;background:#FFF5F5;">'
            )
        return (
            f'<div style="margin-top:6px;"><div class="lbl" style="color:{color};margin-bottom:4px;">{title}</div>'
            f'<div style="display:flex;gap:5px;justify-content:{justify};">{"".join(avatars)}</div></div>'
        )

    def _render_perk_html(self, player: dict) -> str:
        perk_rows = []
        visible_perks = [perk for perk in (player.get("perks_resolved", []) or []) if perk.get("level", 0)]
        for perk in visible_perks[:2]:
            level = perk.get("level", 0)
            perk_name = html.escape(str(perk.get("name", "未知威能")))
            perk_icon = url_to_data_uri(perk.get("icon", ""))
            icon_html = (
                f'<img src="{perk_icon}" alt="{perk_name}" title="{perk_name}" '
                'style="width:28px;height:28px;border-radius:7px;border:1px solid #C9B8AA;background:#D8C6B7;box-shadow:inset 0 0 0 1px rgba(28,25,23,0.08);display:block;">'
                if perk_icon else
                '<div style="width:28px;height:28px;border-radius:7px;border:1px dashed #C9B8AA;background:#D8C6B7;"></div>'
            )
            perk_rows.append(
                f'<div title="{perk_name}" style="position:relative;width:28px;height:28px;flex:0 0 auto;">{icon_html}<span style="position:absolute;right:-5px;bottom:-5px;min-width:14px;height:14px;padding:0 3px;border-radius:999px;background:#4A413B;border:1px solid rgba(255,255,255,0.9);color:#FFF;font-size:9px;font-weight:700;line-height:12px;text-align:center;box-shadow:0 1px 2px rgba(28,25,23,0.18);">{level}</span></div>'
            )
        while len(perk_rows) < 2:
            perk_rows.append(
                '<div title="未选择威能" style="position:relative;width:28px;height:28px;flex:0 0 auto;">'
                '<div style="width:28px;height:28px;border-radius:7px;border:1px dashed #C9B8AA;background:#F4ECE4;display:flex;align-items:center;justify-content:center;color:#A8968F;font-size:11px;font-weight:700;line-height:1;">无</div>'
                '</div>'
            )
        return f'<div style="display:flex;align-items:center;gap:9px;min-width:0;max-width:65px;margin-top:7px;">{"".join(perk_rows)}</div>'

    def _render_player_card(self, player: dict, kda_color: str) -> str:
        def _e(value):
            return html.escape(str(value)) if value not in (None, "") else ""

        name = _e(player.get("name", "未知"))
        hero_guid = str(player.get("heroGuid", ""))
        hero_name = _e(self.format_hero_name(hero_guid))
        hero_icon_src = self.hero_config.get(hero_guid, {}).get("icon") or player.get("heroIcon", "")
        hero_icon = url_to_data_uri(hero_icon_src)
        kill = player.get("kill", 0)
        death = player.get("death", 0)
        assist = player.get("assist", 0)
        final_hit = player.get("finalHit", 0)
        damage = player.get("heroDamage", 0)
        heal = player.get("cure", 0)
        healing_taken = int(player.get("healingTaken", 0) or 0)
        kda = self.calculate_kda(kill, assist, death)
        rank = _e(player.get("rank", "—"))
        perk_html = self._render_perk_html(player)
        return f"""
                <div class="player-card">
                    <div style="display:flex;align-items:flex-start;gap:12px;">
                        <img src="{hero_icon}" alt="{hero_name}" title="{hero_name}" class="hero-avatar">
                        <div style="flex:1;min-width:0;">
                            <div style="display:flex;align-items:center;gap:6px;min-width:0;"><div style="font-size:13px;font-weight:600;color:#1C1917;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div></div>
                            <div style="font-size:11px;color:#A8968F;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{hero_name}</div>{perk_html}
                        </div>
                        <div style="display:flex;align-items:center;flex-shrink:0;padding-top:2px;">
                            <div style="text-align:center;padding:0 12px;border-left:1px solid #EDE8E1;">
                                <div style="font-size:14px;font-weight:600;color:#D4713A;font-variant-numeric:tabular-nums;letter-spacing:-0.02em;line-height:1.1;">{rank}</div>
                                <div style="font-size:9px;color:#A8968F;text-transform:uppercase;letter-spacing:0.06em;margin-top:1px;">段位</div>
                            </div>
                            <div style="text-align:center;padding:0 12px;border-left:1px solid #EDE8E1;">
                                <div style="font-size:19px;font-weight:700;color:{kda_color};font-variant-numeric:tabular-nums;letter-spacing:-0.02em;line-height:1.1;">{kda:.2f}</div>
                                <div style="font-size:9px;color:#A8968F;text-transform:uppercase;letter-spacing:0.06em;margin-top:1px;">KDA</div>
                            </div>
                            <div style="padding:0 0 0 12px;border-left:1px solid #EDE8E1;min-width:132px;">
                                <div style="font-size:11px;color:#6B5C55;margin-bottom:3px;"><span style="color:#A8968F;">K/D/A</span>&ensp;{kill} / {death} / {assist}&emsp;<span style="color:#7C3AED;">终结</span>&ensp;{final_hit}</div>
                                <div style="font-size:11px;color:#6B5C55;margin-bottom:3px;"><span style="color:#D4713A;">伤害</span>&ensp;{damage:,}&emsp;<span style="color:#2F9E44;">治疗</span>&ensp;{heal:,}</div>
                                <div style="font-size:11px;color:#6B5C55;"><span style="color:#A8968F;">受治疗</span>&ensp;{healing_taken:,}</div>
                            </div>
                        </div>
                    </div>
                </div>
            """

    def generate_html_report(self, match_data, output_path=None):
        """生成带威能展示的对局 HTML 报告。"""

        def _e(value):
            return html.escape(str(value)) if value not in (None, "") else ""

        player_name = match_data["player_name"]
        match_id = match_data["match_id"]
        map_name = match_data["map_name"]
        result = match_data["result"]
        match_time = match_data["match_time"]
        game_duration = match_data.get("game_duration", "—")
        teammates = match_data["teammates"]
        enemies = match_data["enemies"]
        team_avg_kda = match_data["team_avg_kda"]
        enemy_avg_kda = match_data["enemy_avg_kda"]
        team_damage = match_data["team_damage"]
        team_heal = match_data["team_heal"]
        enemy_damage = match_data["enemy_damage"]
        enemy_heal = match_data["enemy_heal"]
        team_rank_range = match_data.get("team_rank_range", "—")
        enemy_rank_range = match_data.get("enemy_rank_range", "—")
        banned_heroes = match_data.get("banned_heroes", {"team": [], "enemy": []})

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "match.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()

        result_badge_class = "win-badge" if result == "胜利" else "lose-badge"
        result_border_color = "#2B7A3D" if result == "胜利" else "#B91C1C"

        html_template = html_template.replace("{{PLAYER_NAME}}", _e(player_name))
        html_template = html_template.replace("{{MATCH_ID}}", _e(match_id))
        html_template = html_template.replace("{{MAP_NAME}}", _e(map_name))
        html_template = html_template.replace("{{RESULT}}", _e(result))
        html_template = html_template.replace("{{MATCH_TIME}}", _e(match_time))
        html_template = html_template.replace("{{GAME_DURATION}}", _e(game_duration))
        html_template = html_template.replace("{{TEAM_AVG_KDA}}", f"{team_avg_kda:.2f}")
        html_template = html_template.replace("{{ENEMY_AVG_KDA}}", f"{enemy_avg_kda:.2f}")
        html_template = html_template.replace("{{TEAM_DAMAGE}}", f"{team_damage:,}")
        html_template = html_template.replace("{{TEAM_HEAL}}", f"{team_heal:,}")
        html_template = html_template.replace("{{ENEMY_DAMAGE}}", f"{enemy_damage:,}")
        html_template = html_template.replace("{{ENEMY_HEAL}}", f"{enemy_heal:,}")
        html_template = html_template.replace("{{RESULT_BADGE_CLASS}}", result_badge_class)
        html_template = html_template.replace("{{RESULT_BORDER_COLOR}}", result_border_color)
        html_template = html_template.replace("{{TEAM_RANK_RANGE}}", _e(team_rank_range))
        html_template = html_template.replace("{{ENEMY_RANK_RANGE}}", _e(enemy_rank_range))

        html_template = html_template.replace(
            "{{TEAM_BANNED_HEROES}}",
            self._render_banned_html(banned_heroes.get("team", []), "我方禁用", "#D4713A", "flex-start"),
        )
        html_template = html_template.replace(
            "{{ENEMY_BANNED_HEROES}}",
            self._render_banned_html(banned_heroes.get("enemy", []), "敌方禁用", "#B91C1C", "flex-end"),
        )

        teammates_sorted = sorted(teammates, key=self._role_sort_key)
        enemies_sorted = sorted(enemies, key=self._role_sort_key)
        html_template = html_template.replace(
            "{{TEAM_PLAYERS}}",
            "".join(self._render_player_card(player, "#D4713A") for player in teammates_sorted),
        )
        html_template = html_template.replace(
            "{{ENEMY_PLAYERS}}",
            "".join(self._render_player_card(player, "#B91C1C") for player in enemies_sorted),
        )

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "latest_match_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_template)

        print(f"[成功] 报告已生成: {os.path.basename(output_path)}")
        return output_path

if __name__ == "__main__":
    import sys
    query = LatestMatchQuery()
    if len(sys.argv) > 1:
        player_name = sys.argv[1]
    else:
        player_name = input("\n请输入玩家名称: ").strip()
        if not player_name:
            print("[错误] 玩家名称不能为空")
            sys.exit(1)

    match_data = query.query_latest_match(player_name)
    if match_data:
        query.generate_html_report(match_data)
        webbrowser.open(os.path.abspath("latest_match_report.html"))
    else:
        print("[失败] 无法获取对局数据")
