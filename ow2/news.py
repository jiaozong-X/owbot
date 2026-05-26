"""
活动资讯和英雄调整查询模块
数据来源：https://ow.blizzard.cn/news/patch-notes/live/
"""
import json
import os
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HERO_CONFIG_URL = "https://s.166.net/config/ds_ow/ow_hero_config.json"
NEWS_BASE_URL = "https://ow.blizzard.cn/news/patch-notes/live"
NEWS_URL_LOOKBACK_MONTHS = 12
NEWS_URL_CACHE_TTL = 1800
_HERO_ICON_CACHE = None
_NEWS_URL_CACHE = {"url": None, "expires_at": 0.0}

ROLE_LABELS = {
    "tank": ("防护英雄", "重装", "坦克"),
    "damage": ("输出英雄", "输出", "伤害"),
    "support": ("支援英雄", "支援", "辅助"),
}


def get_hero_icons() -> dict:
    """获取英雄头像映射 {英雄名: 头像URL}"""
    global _HERO_ICON_CACHE
    if _HERO_ICON_CACHE is not None:
        return _HERO_ICON_CACHE

    try:
        req = Request(
            HERO_CONFIG_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _HERO_ICON_CACHE = {
            hero["name"]: hero["icon"] for hero in data if isinstance(hero, dict) and hero.get("name") and hero.get("icon")
        }
        return _HERO_ICON_CACHE
    except Exception:
        return {}


def _iter_recent_news_urls(max_lookback_months: int = NEWS_URL_LOOKBACK_MONTHS):
    current = datetime.now()
    current_serial = current.year * 12 + current.month - 1
    for offset in range(max(1, max_lookback_months)):
        serial = current_serial - offset
        year = serial // 12
        month = serial % 12 + 1
        yield f"{NEWS_BASE_URL}/{year}/{month:02d}/"


def _looks_like_patch_notes_page(html: str) -> bool:
    return "PatchNotes-patchTitle" in html or "补丁说明" in html


def get_news_url(max_lookback_months: int = NEWS_URL_LOOKBACK_MONTHS) -> str:
    """获取最近一个有效的补丁说明 URL。"""
    global _NEWS_URL_CACHE

    now_ts = datetime.now().timestamp()
    cached_url = _NEWS_URL_CACHE.get("url")
    expires_at = float(_NEWS_URL_CACHE.get("expires_at") or 0.0)
    if cached_url and now_ts < expires_at:
        return str(cached_url)

    last_error = None
    for url in _iter_recent_news_urls(max_lookback_months):
        try:
            html = fetch_page(url)
            if not _looks_like_patch_notes_page(html):
                last_error = f"页面结构不匹配: {url}"
                continue
            _NEWS_URL_CACHE = {"url": url, "expires_at": now_ts + NEWS_URL_CACHE_TTL}
            return url
        except Exception as e:
            last_error = e
            if "404" in str(e):
                continue
            break

    raise Exception(f"最近 {max_lookback_months} 个月内未找到可用补丁说明页面: {last_error}")


def fetch_page(url: str = None) -> str:
    """获取网页内容"""
    if url is None:
        url = get_news_url()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except (URLError, HTTPError) as e:
        raise Exception(f"获取页面失败: {e}")


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_patch_blocks(html: str) -> list[str]:
    marker = '<div class="PatchNotes-patch'
    parts = html.split(marker)
    return [marker + part for part in parts[1:]]


def _extract_title_and_date(block: str) -> tuple[str, str]:
    title_match = re.search(r'<h3[^>]*class="PatchNotes-patchTitle"[^>]*>(.*?)</h3>', block, re.S)
    title = _clean_text(title_match.group(1)) if title_match else ""
    date_match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", title)
    return title, (date_match.group(1) if date_match else "")


def _extract_general_updates(block: str) -> list[dict]:
    updates = []
    for match in re.finditer(r'<div class="PatchNotesGeneralUpdate">(.*?)</div>\s*</div>', block, re.S):
        section = match.group(1)
        title_match = re.search(r'<div class="PatchNotesGeneralUpdate-title">(.*?)</div>', section, re.S)
        desc_match = re.search(r'<div class="PatchNotesGeneralUpdate-description">(.*?)</div>', section, re.S)
        title = _clean_text(title_match.group(1)) if title_match else ""
        content = _clean_text(desc_match.group(1)) if desc_match else ""
        if not content:
            lis = [_clean_text(x) for x in re.findall(r"<li>(.*?)</li>", section, re.S)]
            content = "；".join(x for x in lis if x)
        if title and content:
            updates.append({"title": title, "content": content[:500]})
    return updates


def _extract_patch_sections(block: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r'<div class="PatchNotes-section[^"]*"[^>]*>\s*<h4 class="PatchNotes-sectionTitle">(.*?)</h4>(.*?)(?=<div class="PatchNotes-section[^"]*"|$)',
        re.S,
    )
    sections = []
    for match in pattern.finditer(block):
        title = _clean_text(match.group(1))
        if title:
            sections.append((title, match.group(2)))
    return sections


def _section_role(title: str) -> str | None:
    normalized = title.strip()
    for role, labels in ROLE_LABELS.items():
        if normalized in labels:
            return role
    return None


def _extract_hero_updates_from_section(section_html: str, role: str, hero_icons: dict) -> list[dict]:
    hero_changes = []
    blocks = re.split(r'<div class="PatchNotesHeroUpdate">', section_html)
    for raw_block in blocks[1:]:
        hero_block = raw_block.split('<div class="PatchNotesHeroUpdate">', 1)[0]
        name_match = re.search(r'<div class="PatchNotesHeroUpdate-name">(.*?)</div>', hero_block, re.S)
        if not name_match:
            continue

        hero_name = _clean_text(name_match.group(1)).strip('"')
        icon_match = re.search(r'<img[^>]*class="PatchNotesHeroUpdate-icon"[^>]*src="([^"]+)"', hero_block, re.S)
        icon_url = icon_match.group(1).strip() if icon_match else hero_icons.get(hero_name, "")
        dev_match = re.search(r'<div class="PatchNotes-dev[^"]*"[^>]*>\s*<p>(.*?)</p>', hero_block, re.S)
        dev_note = _clean_text(dev_match.group(1)) if dev_match else ""

        abilities = []

        general_match = re.search(
            r'<div class="PatchNotesHeroUpdate-generalUpdates">(.*?)(?=<div class="PatchNotesHeroUpdate-abilitiesList">|$)',
            hero_block,
            re.S,
        )
        if general_match:
            general_html = general_match.group(1)
            for match in re.finditer(r"<p>(.*?)</p>\s*<ul>(.*?)</ul>", general_html, re.S):
                ability_name = _clean_text(match.group(1))
                changes = [_clean_text(x) for x in re.findall(r"<li>(.*?)</li>", match.group(2), re.S)]
                changes = [x for x in changes if x]
                if ability_name or changes:
                    abilities.append({"name": ability_name, "changes": changes})
            if not abilities:
                changes = [_clean_text(x) for x in re.findall(r"<li>(.*?)</li>", general_html, re.S)]
                changes = [x for x in changes if x]
                if changes:
                    abilities.append({"name": "", "changes": changes})

        abilities_match = re.search(r'<div class="PatchNotesHeroUpdate-abilitiesList">(.*)', hero_block, re.S)
        if abilities_match:
            for block in re.split(r'<div class="PatchNotesAbilityUpdate">', abilities_match.group(1))[1:]:
                ability_name_match = re.search(r'<div class="PatchNotesAbilityUpdate-name">(.*?)</div>', block, re.S)
                ability_name = _clean_text(ability_name_match.group(1)) if ability_name_match else ""
                changes = [_clean_text(x) for x in re.findall(r"<li>(.*?)</li>", block, re.S)]
                changes = [x for x in changes if x]
                if ability_name or changes:
                    abilities.append({"name": ability_name, "changes": changes})

        if abilities:
            hero_changes.append(
                {
                    "hero": hero_name,
                    "icon": icon_url,
                    "dev_note": dev_note,
                    "abilities": abilities,
                }
            )

    return hero_changes


def parse_patch_notes(html: str, prefer_hero_patch: bool = False) -> dict:
    """
    解析补丁说明页面，提取活动资讯和英雄调整。
    prefer_hero_patch=True 时，优先选择当前月中第一篇包含英雄调整的补丁。
    """
    result = {
        "title": "",
        "date": "",
        "events": [],
        "hero_update_intro": "",
        "sub_role_updates": [],
        "hero_changes": {"tank": [], "damage": [], "support": []},
        "bugfixes": [],
    }

    blocks = _extract_patch_blocks(html)
    if not blocks:
        return result

    hero_icons = get_hero_icons()
    selected_block = blocks[0]
    if prefer_hero_patch:
        for block in blocks:
            if any(_section_role(title) for title, _ in _extract_patch_sections(block)):
                selected_block = block
                break

    title, date = _extract_title_and_date(selected_block)
    result["title"] = title
    result["date"] = date
    result["events"] = _extract_general_updates(selected_block)

    intro_set = False
    for section_title, section_html in _extract_patch_sections(selected_block):
        role = _section_role(section_title)
        if role:
            heroes = _extract_hero_updates_from_section(section_html, role, hero_icons)
            if heroes:
                result["hero_changes"][role].extend(heroes)
                if not intro_set:
                    dev_match = re.search(r'<div class="PatchNotes-dev[^"]*"[^>]*>\s*<p>(.*?)</p>', section_html, re.S)
                    result["hero_update_intro"] = _clean_text(dev_match.group(1)) if dev_match else ""
                    intro_set = True
            continue

        if section_title == "英雄更新":
            dev_match = re.search(r'<div class="PatchNotes-dev[^"]*"[^>]*>\s*<p>(.*?)</p>', section_html, re.S)
            result["hero_update_intro"] = _clean_text(dev_match.group(1)) if dev_match else result["hero_update_intro"]
            intro_set = intro_set or bool(result["hero_update_intro"])
            for item in _extract_general_updates(section_html):
                result["sub_role_updates"].append(
                    {
                        "name": item["title"],
                        "changes": [item["content"]],
                        "dev_note": "",
                    }
                )

        if section_title == "错误修复":
            fixes = [_clean_text(x) for x in re.findall(r"<li>(.*?)</li>", section_html, re.S)]
            result["bugfixes"].extend(x for x in fixes if x and len(x) > 5)

    return result


def get_news() -> dict:
    """获取活动资讯"""
    html = fetch_page()
    data = parse_patch_notes(html)

    events = [event for event in data.get("events", []) if len(event.get("content", "")) > 20]
    if not events and data.get("hero_update_intro"):
        events.append({"title": "英雄更新", "content": data["hero_update_intro"][:500]})

    if not events:
        hero_changes = data.get("hero_changes", {})
        summaries = []
        role_names = {"tank": "重装", "damage": "输出", "support": "支援"}
        for role in ("tank", "damage", "support"):
            count = len(hero_changes.get(role, []))
            if count:
                summaries.append(f"{role_names[role]} {count} 位英雄调整")
        if summaries:
            events.append({"title": "平衡更新摘要", "content": "；".join(summaries)})

    return {"title": data.get("title", ""), "date": data.get("date", ""), "events": events}


def get_patch_notes() -> dict:
    """获取英雄调整"""
    html = fetch_page()
    data = parse_patch_notes(html, prefer_hero_patch=True)

    return {
        "title": data.get("title", ""),
        "date": data.get("date", ""),
        "hero_update_intro": data.get("hero_update_intro", ""),
        "sub_role_updates": data.get("sub_role_updates", []),
        "hero_changes": data.get("hero_changes", {"tank": [], "damage": [], "support": []}),
        "bugfixes": data.get("bugfixes", [])[:15],
    }


def generate_news_html(news_data: dict, output_path: str):
    """生成活动资讯 HTML"""
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "news.html")

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("{{TITLE}}", news_data.get("title", "活动资讯"))
    html = html.replace("{{DATE}}", news_data.get("date", ""))

    events_html = ""
    for i, event in enumerate(news_data.get("events", []), 1):
        events_html += f'''
        <div class="event-item">
            <div class="event-num">{i}</div>
            <div class="event-content">
                <div class="event-title">{event["title"]}</div>
                <div class="event-desc">{event["content"]}</div>
            </div>
        </div>'''

    html = html.replace("{{EVENTS}}", events_html)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def generate_patch_html(patch_data: dict, output_path: str):
    """生成英雄调整 HTML"""
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "patchnotes.html")

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("{{TITLE}}", patch_data.get("title", "英雄调整"))
    html = html.replace("{{DATE}}", patch_data.get("date", ""))

    intro = patch_data.get("hero_update_intro", "")
    intro_html = f'<div class="intro-section"><p>{intro}</p></div>' if intro else ""
    html = html.replace("{{HERO_UPDATE_INTRO}}", intro_html)

    sub_role_html = ""
    for sub_role in patch_data.get("sub_role_updates", []):
        changes_list = "".join(f"<li>{c}</li>" for c in sub_role.get("changes", []))
        dev_note = sub_role.get("dev_note", "")
        dev_note_html = f'<p class="dev-note">{dev_note}</p>' if dev_note else ""
        sub_role_html += f'''
        <div class="sub-role-card">
            <div class="sub-role-name">{sub_role["name"]}</div>
            <ul class="change-list">{changes_list}</ul>
            {dev_note_html}
        </div>'''
    html = html.replace("{{SUB_ROLE_UPDATES}}", sub_role_html)

    role_labels = {"tank": "重装", "damage": "输出", "support": "支援"}

    for role in ["tank", "damage", "support"]:
        role_html = ""
        heroes = patch_data.get("hero_changes", {}).get(role, [])
        for hero_data in heroes:
            icon_url = hero_data.get("icon", "")
            if icon_url:
                hero_name_html = f'<img class="hero-icon" src="{icon_url}" alt="{hero_data["hero"]}">{hero_data["hero"]}'
            else:
                hero_name_html = hero_data["hero"]

            dev_note = hero_data.get("dev_note", "")
            dev_note_html = f'<p class="dev-note">{dev_note}</p>' if dev_note else ""

            abilities_html = ""
            for ability in hero_data.get("abilities", []):
                ability_name = ability.get("name", "")
                ability_changes = ability.get("changes", [])
                if ability_changes:
                    changes_list = "".join(f"<li>{c}</li>" for c in ability_changes)
                    if ability_name:
                        abilities_html += (
                            f'<div class="ability-block"><span class="ability-name">{ability_name}</span>'
                            f'<ul class="change-list">{changes_list}</ul></div>'
                        )
                    else:
                        abilities_html += f'<ul class="change-list">{changes_list}</ul>'

            role_html += f'''
            <div class="hero-card">
                <div class="hero-name">{hero_name_html}</div>
                {dev_note_html}
                {abilities_html}
            </div>'''

        html = html.replace(f"{{{{ROLE_{role.upper()}}}}}", role_html)
        html = html.replace(f"{{{{ROLE_{role.upper()}_LABEL}}}}", role_labels[role])

    bugfixes_html = ""
    for fix in patch_data.get("bugfixes", []):
        bugfixes_html += f"<li>{fix}</li>"
    html = html.replace("{{BUGFIXES}}", bugfixes_html)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


class NewsQuery:
    """活动资讯查询类"""

    def __init__(self):
        self._cache = None
        self._cache_time = 0
        self._cache_ttl = 3600

    def query_news(self):
        """查询活动资讯"""
        now = datetime.now().timestamp()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        try:
            data = get_news()
            self._cache = data
            self._cache_time = now
            return data
        except Exception as e:
            raise Exception(f"获取活动资讯失败: {e}")

    def query_patch_notes(self):
        """查询英雄调整"""
        try:
            return get_patch_notes()
        except Exception as e:
            raise Exception(f"获取英雄调整失败: {e}")

    def generate_news_report(self, output_path: str) -> bool:
        """生成活动资讯报告"""
        data = self.query_news()
        if not data or not data.get("events"):
            return False
        generate_news_html(data, output_path)
        return True

    def generate_patch_report(self, output_path: str) -> bool:
        """生成英雄调整报告"""
        data = self.query_patch_notes()
        if not data:
            return False
        generate_patch_html(data, output_path)
        return True
