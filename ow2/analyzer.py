import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .client import OW2Client, url_to_data_uri

sys.stdout.reconfigure(encoding="utf-8")


class MatchStrengthAnalyzer(OW2Client):
    def __init__(self):
        super().__init__()

        self.rank_weights = {
            "None": 0, "Bronze": 1, "Silver": 2, "Gold": 3,
            "Platinum": 4, "Diamond": 5, "Master": 6,
            "Grandmaster": 7, "Champion": 8, "Apex": 9,
        }

        # 英雄名映射继承自 OW2Client

    def format_hero_name(self, guid: str) -> str:
        return self.hero_guid_names.get(str(guid), guid)

    def _find_self_player(self, teammates, customer_token, player_name):
        for player in teammates:
            if player.get("customerToken") == customer_token:
                return player
        for player in teammates:
            if player.get("name") == player_name:
                return player
        return teammates[0] if teammates else None

    def calculate_rank_weight(self, rank_name, rank_sub_tier, rank_score):
        base_weight = self.rank_weights.get(rank_name, 0)
        sub_weight = (6 - rank_sub_tier) * 0.2 if rank_sub_tier > 0 else 0
        score_weight = rank_score * 0.001
        return base_weight + sub_weight + score_weight

    def calculate_team_average(self, ranks):
        if not ranks:
            return 0
        total = sum(
            self.calculate_rank_weight(r["rankName"], r["rankSubTier"], r["rankScore"])
            for r in ranks
        )
        return total / len(ranks)

    def get_grade(self, diff, is_win):
        """
        评分系统：结合段位差异和胜负结果
        - 以弱胜强：高分（荣耀）
        - 以强败弱：低分（耻辱）
        """
        if is_win:
            # 胜利：敌方越强，评分越高
            if diff < -1.0:
                return ("S", "以弱胜强")       # 翻盘荣耀
            elif diff < -0.5:
                return ("A", "硬仗获胜")       # 势均力敌略处下风
            elif diff < 0:
                return ("B+", "稳健取胜")     # 双方差不多
            elif diff < 0.5:
                return ("B", "正常发挥")      # 略占优势
            elif diff < 1.0:
                return ("C", "优势局")        # 明显优势
            else:
                return ("D", "碾压局")        # 段位碾压
        else:
            # 失败：敌方越弱，评分越低
            if diff < -1.0:
                return ("C", "尽力了")        # 敌方很强，输了正常
            elif diff < -0.5:
                return ("D", "惜败")          # 敌方略强
            elif diff < 0:
                return ("E", "发挥失常")      # 双方差不多却输了
            elif diff < 0.5:
                return ("F", "不该输")        # 我方略强却输了
            else:
                return ("F", "耻辱")          # 段位碾压却输了

    def analyze_matches(self, player_name, output_path=None, game_mode="leisure"):
        player_info = self.search_player(player_name)
        if not player_info:
            return None

        customer_token = player_info["customerToken"]
        matches = self.get_match_list(customer_token, game_mode=game_mode)
        if not matches:
            return None

        target_matches = matches[:12]

        # 并发获取 12 场 match_info
        match_infos = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_match = {
                executor.submit(self.get_match_info, m.get("matchId"), customer_token): m
                for m in target_matches
            }
            for future in as_completed(future_to_match):
                m = future_to_match[future]
                match_infos[m.get("matchId")] = future.result()

        # 第一遍：计算 KDA，收集所有需要查询段位的 token
        # token_jobs: list of (token, side, match_id, player_idx)
        token_jobs = []
        pre_analysis = []  # 按顺序存 (match, teammates_with_kda, enemies_with_kda)

        for match in target_matches:
            match_id = match.get("matchId")
            match_info = match_infos.get(match_id)
            if not match_info:
                pre_analysis.append(None)
                continue

            teammates = match_info.get("teammateList", [])
            enemies = match_info.get("enemyList", [])

            for p in teammates:
                p["kda"] = self.calculate_kda(p.get("kill", 0), p.get("assist", 0), p.get("death", 0))
            for p in enemies:
                p["kda"] = self.calculate_kda(p.get("kill", 0), p.get("assist", 0), p.get("death", 0))

            for p in teammates:
                token = p.get("customerToken")
                if token:
                    token_jobs.append((token, "team", match_id, p.get("name")))
            for p in enemies:
                token = p.get("customerToken")
                if token:
                    token_jobs.append((token, "enemy", match_id, p.get("name")))

            pre_analysis.append((match, teammates, enemies))

        # 一次性并发获取所有段位（最多约 120 个）
        rank_results = {}  # token -> rank_dict
        unique_tokens = list({job[0] for job in token_jobs})

        def fetch_rank(token):
            return token, self.get_player_rank(token)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_rank, tok): tok for tok in unique_tokens}
            for future in as_completed(futures):
                token, rank = future.result()
                rank_results[token] = rank

        # 第二遍：组装结果
        match_analysis = []
        hero_counter = Counter()

        for idx, item in enumerate(pre_analysis):
            if item is None:
                continue
            match, teammates, enemies = item
            match_id = match.get("matchId")
            map_guid = str(match.get("mapGuid", ""))
            map_name = self.map_names.get(map_guid, "未知地图")
            match_ret = match.get("matchRet", 0)
            match_result = "胜利" if match_ret == 1 else "失败"

            team_kdas = [p["kda"] for p in teammates]
            enemy_kdas = [p["kda"] for p in enemies]
            team_avg_kda = sum(team_kdas) / len(team_kdas) if team_kdas else 0
            enemy_avg_kda = sum(enemy_kdas) / len(enemy_kdas) if enemy_kdas else 0

            team_ranks = []
            for p in teammates:
                tok = p.get("customerToken")
                if tok and tok in rank_results:
                    team_ranks.append(rank_results[tok])

            enemy_ranks = []
            for p in enemies:
                tok = p.get("customerToken")
                if tok and tok in rank_results:
                    enemy_ranks.append(rank_results[tok])

            # 过滤无段位，仅用有段位的玩家计算 max/min
            team_ranked  = [r for r in team_ranks  if r["rankName"] != "None"]
            enemy_ranked = [r for r in enemy_ranks if r["rankName"] != "None"]

            # 统计玩家本人使用的英雄（teammates[0] 通常是本人，但为全量统计改为遍历 teammates）
            self_player = self._find_self_player(teammates, customer_token, player_info["name"])
            hero_guid = str(self_player.get("heroGuid", "")) if self_player else ""
            hero_name = self.format_hero_name(hero_guid) if hero_guid else "未知英雄"
            hero_icon = ""
            if hero_guid:
                hero_icon = self.hero_config.get(hero_guid, {}).get("icon") or self_player.get("heroIcon", "")
                hero_counter[hero_guid] += 1

            _none_rank = {"rankName": "None", "rankSubTier": 0, "rankScore": 0}
            team_max_rank  = max(team_ranked,  key=lambda x: x["totalScore"]) if team_ranked  else _none_rank
            team_min_rank  = min(team_ranked,  key=lambda x: x["totalScore"]) if team_ranked  else _none_rank
            enemy_max_rank = max(enemy_ranked, key=lambda x: x["totalScore"]) if enemy_ranked else _none_rank
            enemy_min_rank = min(enemy_ranked, key=lambda x: x["totalScore"]) if enemy_ranked else _none_rank

            team_avg_rank = self.calculate_team_average(team_ranks)
            enemy_avg_rank = self.calculate_team_average(enemy_ranks)
            rank_diff = team_avg_rank - enemy_avg_rank
            is_win = match_result == "胜利"
            grade, grade_desc = self.get_grade(rank_diff, is_win)

            def format_rank(rank):
                if rank["rankName"] != "None":
                    cn = self.rank_cn.get(rank["rankName"], rank["rankName"])
                    return f"{cn}{rank['rankSubTier']}" if rank["rankSubTier"] > 0 else cn
                return "—"

            match_analysis.append({
                "match_id": match_id,
                "map_name": map_name,
                "result": match_result,
                "hero_name": hero_name,
                "hero_icon": url_to_data_uri(hero_icon) if hero_icon else "",
                "team_avg_kda": team_avg_kda,
                "enemy_avg_kda": enemy_avg_kda,
                "team_avg_rank": team_avg_rank,
                "enemy_avg_rank": enemy_avg_rank,
                "rank_diff": rank_diff,
                "grade": grade,
                "grade_desc": grade_desc,
                "team_max_rank": format_rank(team_max_rank),
                "team_min_rank": format_rank(team_min_rank),
                "enemy_max_rank": format_rank(enemy_max_rank),
                "enemy_min_rank": format_rank(enemy_min_rank),
                "team_ranks": team_ranks,
                "enemy_ranks": enemy_ranks,
            })

        if not match_analysis:
            return None

        # 汇总统计
        win_count = sum(1 for m in match_analysis if m["result"] == "胜利")
        win_rate = win_count / len(match_analysis) if match_analysis else 0

        # best KDA match
        best_kda_match = max(match_analysis, key=lambda m: m["team_avg_kda"])

        # hero stats: top heroes by guid -> readable name
        hero_stats = []
        for guid, count in hero_counter.most_common(5):
            hero_stats.append({"guid": guid, "name": self.format_hero_name(guid), "count": count})

        # 段位分布统计
        rank_stats = {}
        for match in match_analysis:
            for rank in match["team_ranks"] + match["enemy_ranks"]:
                rank_name = rank["rankName"]
                rank_cn = self.rank_cn.get(rank_name, rank_name)
                rank_stats[rank_cn] = rank_stats.get(rank_cn, 0) + 1

        self.generate_html_report(
            player_info["name"], match_analysis, rank_stats,
            win_count=win_count,
            win_rate=win_rate,
            hero_stats=hero_stats,
            best_kda_match=best_kda_match,
            output_path=output_path,
        )
        return match_analysis

    def generate_html_report(self, player_name, match_analysis, rank_stats,
                             win_count=0, win_rate=0.0,
                             hero_stats=None, best_kda_match=None,
                             output_path=None):
        """生成 HTML 报告"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        labels = [f"{i+1}.{m['map_name']}({'胜' if m['result']=='胜利' else '负'})" for i, m in enumerate(match_analysis)]
        team_avg_data = [m["team_avg_rank"] for m in match_analysis]
        enemy_avg_data = [m["enemy_avg_rank"] for m in match_analysis]
        diff_data = [m["rank_diff"] for m in match_analysis]

        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "strength.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()

        html = html.replace("{{PLAYER_NAME}}", player_name)
        html = html.replace("{{TIMESTAMP}}", now)
        html = html.replace("{{LABELS}}", json.dumps(labels))
        html = html.replace("{{TEAM_DATA}}", json.dumps(team_avg_data))
        html = html.replace("{{ENEMY_DATA}}", json.dumps(enemy_avg_data))
        html = html.replace("{{DIFF_DATA}}", json.dumps(diff_data))
        html = html.replace("{{HERO_STATS_JSON}}", json.dumps(hero_stats or []))
        html = html.replace("{{BEST_KDA_JSON}}", json.dumps(best_kda_match or {}))

        # 表格行
        table_rows = ""
        for idx, match in enumerate(match_analysis, 1):
            result_style = "color:#2B7A3D;font-weight:600;" if match["result"] == "胜利" else "color:#B91C1C;"
            diff_sign = "+" if match["rank_diff"] >= 0 else ""
            diff_style = "color:#D4713A;font-weight:600;" if match["rank_diff"] >= 0 else "color:#A8968F;"
            grade_styles = {
                "S":   "background:#FBF0E8;color:#9A3412;",
                "A":   "background:#EBFBEE;color:#2B7A3D;",
                "B+":  "background:#D1FAE5;color:#059669;",
                "B":   "background:#EFF6FF;color:#1E40AF;",
                "C":   "background:#F5F4F2;color:#6B5C55;",
                "D":   "background:#FFF7ED;color:#C2410C;",
                "E":   "background:#FEF3C7;color:#B45309;",
                "F":   "background:#FFF5F5;color:#991B1B;",
            }
            grade_style = grade_styles.get(match["grade"], "background:#F5F4F2;color:#6B5C55;")
            match_id = match.get("match_id", "未知")
            match_id_short = match_id[:8] + "..." if len(match_id) > 8 else match_id
            hero_icon = match.get("hero_icon", "")
            hero_name = match.get("hero_name", "未知英雄")
            hero_cell = (
                f'<img src="{hero_icon}" alt="{hero_name}" title="{hero_name}" '
                'style="width:28px;height:28px;border-radius:8px;border:1px solid #E5DDD4;object-fit:cover;background:#F8F6F2;display:block;margin:0 auto;">'
                if hero_icon else
                '<div style="width:28px;height:28px;border-radius:8px;border:1px dashed #E5DDD4;background:#F8F6F2;margin:0 auto;"></div>'
            )
            table_rows += f"""
                    <tr>
                        <td style="color:#A8968F;">{idx}</td>
                        <td style="text-align:center;">{hero_cell}</td>
                        <td style="color:#1C1917;font-weight:500;">{match["map_name"]}</td>
                        <td>
                            <button onclick="copyToClipboard('{match_id}')"
                                    style="font-family:ui-monospace,monospace;font-size:11px;color:#A8968F;cursor:pointer;background:none;border:none;padding:0;transition:color 0.15s;"
                                    onmouseover="this.style.color='#D4713A'" onmouseout="this.style.color='#A8968F'"
                                    title="点击复制完整 ID: {match_id}">
                                {match_id_short}
                            </button>
                        </td>
                        <td style="{result_style}">{match["result"]}</td>
                        <td style="text-align:right;color:#1C1917;font-variant-numeric:tabular-nums;">{match["team_avg_rank"]:.2f}</td>
                        <td style="text-align:right;color:#1C1917;font-variant-numeric:tabular-nums;">{match["enemy_avg_rank"]:.2f}</td>
                        <td style="text-align:right;font-variant-numeric:tabular-nums;{diff_style}">{diff_sign}{match["rank_diff"]:.2f}</td>
                        <td style="color:#A8968F;font-size:12px;">{match["team_max_rank"]}/{match["team_min_rank"]}</td>
                        <td style="color:#A8968F;font-size:12px;">{match["enemy_max_rank"]}/{match["enemy_min_rank"]}</td>
                        <td style="text-align:center;">
                            <span style="display:inline-block;padding:3px 10px;border-radius:6px;font-size:12px;font-weight:600;{grade_style}" title="{match["grade_desc"]}">
                                {match["grade"]}
                            </span>
                        </td>
                    </tr>
            """
        html = html.replace("{{TABLE_ROWS}}", table_rows)

        # 段位分布
        rank_stats_html = ""
        rank_order = ["青铜", "白银", "黄金", "铂金", "钻石", "大师", "宗师", "冠军", "英杰"]
        total_players = sum(rank_stats.get(r, 0) for r in rank_order)
        for rank_name in rank_order:
            count = rank_stats.get(rank_name, 0)
            if count > 0:
                pct = (count / total_players * 100) if total_players > 0 else 0
                rank_stats_html += f"""
                <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                    <div style="width:48px;text-align:right;font-size:12px;color:#6B5C55;flex-shrink:0;">{rank_name}</div>
                    <div style="flex:1;height:7px;background:#EDE8E1;border-radius:4px;overflow:hidden;">
                        <div style="height:100%;width:{pct:.1f}%;background:#D4713A;border-radius:4px;"></div>
                    </div>
                    <div style="width:28px;font-size:12px;font-weight:600;color:#1C1917;font-variant-numeric:tabular-nums;">{count}</div>
                </div>
                """
        html = html.replace("{{RANK_STATS}}", rank_stats_html)

        if output_path is None:
            output_path = os.path.join(os.path.dirname(__file__), "..", "match_strength_report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"\n[成功] HTML 报告已生成: {output_path}")
        return output_path


if __name__ == "__main__":
    analyzer = MatchStrengthAnalyzer()
    if len(sys.argv) > 1:
        player_name = sys.argv[1].strip()
    else:
        player_name = input("\n请输入玩家名称（例如：就酱#51561）: ").strip()
    if player_name:
        analyzer.analyze_matches(player_name)
    else:
        print("[错误] 未输入玩家名称")
