import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT_DIR, "data", "index")
AWARDS_JSON = os.path.join(INDEX_DIR, "awards.json")
USERGROUPS_JSON = os.path.join(INDEX_DIR, "usergroups.json")

IMAGE_EXT_RE = re.compile(r"\.(png|gif|jpe?g|webp|ico|svg)$", re.I)
GLUED_IMG_TEXT_RE = re.compile(
    r"^((?:https?://(?:[\w.-]+\.)?v3rmillion\.net)?/?images/[\w./\\-]+\.(?:png|gif|jpe?g|webp|ico|svg))(.+)?$",
    re.IGNORECASE,
)

# (name, description, image path with backslashes as in SQL)
AWARDS_DATA: List[Tuple[str, str, str]] = [
    ("Staff", "This user is currently a member of the staff team.", r"images\awards\Staff.png"),
    ("Christmas 2020", "Celebrated Christmas 2020 with us by posting on a specific thread.", r"images\awards\Christmas2020.png"),
    ("Christmas 2019", "Celebrated Christmas 2019 with us by posting on a specific thread.", r"images\awards\Christmas2019.png"),
    ("Christmas 2018", "Celebrated Christmas 2018 with us by posting on a specific thread.", r"images\awards\Christmas2018.png"),
    ("Christmas 2017 ", "Celebrated Christmas 2017 with us by posting on a specific thread.", r"images\awards\Christmas2017.png"),
    ("Christmas 2016", "Celebrated Christmas 2016 with us by posting on a specific thread.", r"images\awards\Christmas2016.png"),
    ("Like a Mod", "Get liked by an mod.", r"images\awards\like_a_mod.png"),
    ("Like a Boss", "Achieving 500 likes.", r"images\awards\like_a_boss.png"),
    ("Gifter", "Gift a user VIP or Elite.", r"images\awards\gifter.png"),
    ("Eso Community", "Eso community member.", r"images\awards\EsoCommunity.png"),
    ("Ex Eso Member", "Ex Eso member.", r"images\awards\EsoMember.png"),
    ("Ex Staff", "Ex staff member.", r"images\awards\ExStaff.png"),
    ("Pride Charity", "Donated during the pride charity fundraiser.", r"images\awards\PrideCharity.png"),
    ("Respected by 1x1x1x1", "Respected by 1x1x1x1", r"images\awards\respected2.png"),
    ("Spooktober", "Celebrated spooktober with us by posting on a specific thread.", r"images\awards\Spooktober.png"),
    ("Clover", "Lucky", r"images\awards\clover.png"),
    ("Chatbox Maniac", "Conversation starter.", r"images\awards\ChatboxManiac.png"),
    ("1x1music", "Have the same music taste with 1x1x1x1.", r"images\awards\1x1music.png"),
    ("Carlos Family", "Apart of Carlos family.", r"images\awards\CarlosFamily.png"),
    ("Grammar King", "Corrects peoples grammar.", r"images\awards\GrammarKing.png"),
    ("Handy Job", "Gave a helping hand.", r"images\awards\handyjob.png"),
    ("Terminal", "Programmar", r"images\awards\Terminal.png"),
    ("Segfault", "Segs friend", r"images\awards\Segfault.png"),
    ("Hardware Badge", "PC hardware master.", r"images\awards\HardwareBadge.png"),
    ("Two Factors of Awesome", "Enabled 2fa.", r"images\awards\2fa.png"),
    ("5 Year", "Celebrated 5 years with us.", r"images\awards\5year.png"),
    ("Beta Tester", "Beta Tester", r"images\awards\beta.png"),
    ("Elite", "Purchase Elite.", r"images\awards\Elite.png"),
    ("One Million", "Has over one million likes.", r"images\awards\OneMillion.png"),
    ("Dove", "Dove", r"images\awards\dove.png"),
    ("Game Badge", "Gamer", r"images\awards\VGameBadge.png"),
    ("Star", "Star", r"images\awards\V-Star.png"),
    ("Texas Toast", "Texas Toast", r"images\awards\TexasToast.png"),
    ("Deputy", "Community Deputy", r"images\awards\Deputy.png"),
    ("Caeso Competition Winner", "Caeso competition winner.", r"images\awards\CaesoCompWinner.png"),
    ("Silver Donor", "2nd highest donor.", r"images\awards\SilverDonor.png"),
    ("Watcha's Competition Winner", "Watcha's Competition Winner", r"images\awards\watchasstupidicon.png"),
    ("Seby Competition Winner", "Seby Competition Winner", r"images\awards\SebyCompWinner.png"),
    ("Gold Donor", "1st highest donor.", r"images\awards\GoldDonor.png"),
    ("Graphics Guru", "Graphics artist.", r"images\awards\GraphicsGuru.png"),
    ("Scavenger", "Participated in the egg hunt.", r"images\awards\scavenger.png"),
    ("Bonze Donor", "3rd highest donor.", r"images\awards\BronzeDonor.png"),
    ("Criss Ting", "High", r"images\awards\criss_ting.png"),
    ("Fires Award", "Fire award.", r"images\awards\FiresAward.png"),
    ("Literature Badge", "Won the literature contest.", r"images\awards\LiteratureBadge.png"),
    ("Bucked 7", "Bucked 7", r"images\awards\bucked7.png"),
    ("One Million Threads", "One millionth thread.", r"images\awards\OneMillionThreads.png"),
    ("Christmas 2022", "Celebrated Christmas 2022 with us by posting on a specific thread.", r"images\awards\Christmas2022.png"),
    ("Christmas 2021", "Celebrated Christmas 2021 with us by posting on a specific thread.", r"images\awards\the_right.png"),
    ("Raccoon Mod", "Raccoon Mod", r"images\awards\Raccoon.png"),
    ("V3rmillion Missionary", "V3rmillion Missionary", r"images\awards\vmaward.png"),
]

USERGROUPS_DATA: List[Dict[str, Any]] = [
    {"gid": 1, "title": "Guests", "usertitle": "Unregistered", "namestyle": "{username}", "image": "", "stars": 0, "starimage": ""},
    {"gid": 2, "title": "Members", "usertitle": "", "namestyle": '<span style="color: #CD1818 ;"><strong>{username}</strong></span>', "image": "", "stars": 1, "starimage": "images/star.png"},
    {"gid": 3, "title": "Head Moderators", "usertitle": "Head Moderators", "namestyle": '<span style="color: #0099FF; text-shadow: 1px 1px 6px #8F8F8F;"><bold>{username}</bold></span>', "image": "images/UserBars/Redesigned/Head_Mod.png", "stars": 6, "starimage": "images/star.png"},
    {"gid": 4, "title": "Administrators", "usertitle": "Administrator", "namestyle": '<span style="color: #B40404;text-shadow: 0px 3px 6px rgba(188, 150, 150, 0.48); font-weight:bold;">{username}</span>', "image": "images/UserBars/Redesigned/Administrator.png", "stars": 7, "starimage": "images/star.png"},
    {"gid": 5, "title": "Account not Activated", "usertitle": "Account not Activated", "namestyle": '<span style="color: #BFBFBF;"><strong>{username}</strong></span>', "image": "", "stars": 0, "starimage": "images/star.png"},
    {"gid": 6, "title": "Moderators", "usertitle": "Moderator", "namestyle": '<span style="color: #0099FF;"><bold>{username}</bold></span>', "image": "images/UserBars/Redesigned/Moderator.png", "stars": 5, "starimage": "images/star.png"},
    {"gid": 7, "title": "Banned", "usertitle": "Banned", "namestyle": '<span style="color: #000000;"><strong><s>{username}</s></strong></span>', "image": "", "stars": 0, "starimage": "images/star.png"},
    {"gid": 14, "title": "Esoterica", "usertitle": "Esoterica", "namestyle": '<span style="color:#c77138;"><strong>{username}</strong></span>', "image": "images/UserBars/Redesigned/Esoterica.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 13, "title": "Owner", "usertitle": "", "namestyle": '<span style="color: #B40404;text-shadow: 0px 3px 6px rgba(188, 150, 150, 0.48); font-weight:bold;">{username}</span>', "image": "images/UserBars/Redesigned/Owner.png", "stars": 12, "starimage": "images/star.png"},
    {"gid": 10, "title": "Vip", "usertitle": "", "namestyle": '<span style="color:#29CB09;">{username}</span>', "image": "images/UserBars/Redesigned/VIP.png", "stars": 5, "starimage": "images/star.png"},
    {"gid": 11, "title": "Elite", "usertitle": "Elite", "namestyle": '<span style="color:#FFC800;"><strong>{username}</strong></span>', "image": "images/UserBars/Redesigned/Elite.png", "stars": 5, "starimage": "images/star.png"},
    {"gid": 12, "title": "Synapse", "usertitle": "Synapse", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Redesigned/Synapse.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 15, "title": "Advisor", "usertitle": "", "namestyle": '<span style="color: #F763FF;">{username}</span>', "image": "images/UserBars/Redesigned/Advisor.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 16, "title": "Vermillion Missionary", "usertitle": "Vermillion Missionary", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/V3rmillionMissionary_Dec.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 18, "title": "Renzi", "usertitle": "Renzi", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/Renzi.png", "stars": 2, "starimage": "images/star.png"},
    {"gid": 19, "title": "Scriptware", "usertitle": "Scriptware", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Redesigned/Scriptware.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 20, "title": "Nihon", "usertitle": "Nihon", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/nihonug.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 21, "title": "Celery", "usertitle": "Celery", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/celeryug.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 22, "title": "Robot", "usertitle": "", "namestyle": "{username}", "image": "", "stars": 0, "starimage": "images/star.png"},
    {"gid": 23, "title": "Developer", "usertitle": "", "namestyle": '<span style="color: #04B485;text-shadow: 0px 3px 6px rgba(129, 173, 161, 0.48); font-weight:bold;">{username}</span>', "image": "images/UserBars/Redesigned/Developer.png", "stars": 5, "starimage": "images/star.png"},
    {"gid": 24, "title": "Rain", "usertitle": "Rain", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/Rain.png", "stars": 0, "starimage": "images/star.png"},
    {"gid": 25, "title": "Alvaria", "usertitle": "Alvaria", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/Alvaria.png", "stars": 0, "starimage": "images/star.png"},
]

_awards_by_filename: Optional[Dict[str, Dict[str, str]]] = None
_awards_by_name: Optional[Dict[str, Dict[str, str]]] = None
_usergroups_by_image: Optional[Dict[str, Dict[str, Any]]] = None
_usergroups_by_title: Optional[Dict[str, Dict[str, Any]]] = None


def _sql_image_to_web(path: str) -> str:
    return "/static/" + path.replace("\\", "/").lstrip("/")


def build_awards_index() -> Dict[str, Any]:
    by_filename: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}
    for name, description, image in AWARDS_DATA:
        filename = os.path.basename(image.replace("\\", "/"))
        entry = {
            "name": name.strip(),
            "description": description,
            "filename": filename,
            "image": _sql_image_to_web(image),
        }
        by_filename[filename.lower()] = entry
        by_name[name.strip().lower()] = entry
    return {
        "_note": "Award image paths mapped from mybb_ougc_awards SQL dump.",
        "by_filename": by_filename,
        "by_name": by_name,
    }


def build_usergroups_index() -> Dict[str, Any]:
    by_gid = {str(g["gid"]): g for g in USERGROUPS_DATA}
    by_image: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    by_usertitle: Dict[str, Dict[str, Any]] = {}
    for group in USERGROUPS_DATA:
        if group.get("image"):
            key = group["image"].replace("\\", "/").lower()
            by_image[key] = group
            by_image[os.path.basename(key)] = group
        by_title[group["title"].lower()] = group
        if group.get("usertitle"):
            by_usertitle[group["usertitle"].lower()] = group
    return {
        "_note": "gid values may not be faithful to original v3rmillion archive.",
        "by_gid": by_gid,
        "by_image": by_image,
        "by_title": by_title,
        "by_usertitle": by_usertitle,
        "groups": USERGROUPS_DATA,
    }


def write_index_files() -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(AWARDS_JSON, "w", encoding="utf-8") as f:
        json.dump(build_awards_index(), f, ensure_ascii=False, indent=2)
    with open(USERGROUPS_JSON, "w", encoding="utf-8") as f:
        json.dump(build_usergroups_index(), f, ensure_ascii=False, indent=2)


def normalize_image_path(src: str) -> str:
    if not src:
        return src

    src = src.strip().replace("\\", "/")

    # fix old archive smiley paths
    src = src.replace(
        "/static/images/smilies/new/",
        "/static/images/smilies/"
    )

    src = re.sub(
        r"^https?://(?:www\.)?v3rmillion\.net",
        "",
        src,
        flags=re.I
    )

    if src.startswith("images/"):
        src = "/" + src

    if not src.startswith("/"):
        if IMAGE_EXT_RE.search(src):
            src = "/images/" + src.lstrip("/")
        else:
            return src

    basename = os.path.basename(src.split("?")[0])

    awards = build_awards_index()["by_filename"]
    if basename.lower() in awards:
        return awards[basename.lower()]["image"]

    if "/images/awddecals/" in src.lower():
        mapped = basename
        if mapped.lower() in awards:
            return awards[mapped.lower()]["image"]

    if src.startswith("/images/"):
        return "/static" + src

    return src

def split_glued_image_text(text: str) -> Tuple[Optional[str], str]:
    if not text:
        return None, text or ""
    match = GLUED_IMG_TEXT_RE.match(text.strip())
    if not match:
        return None, text
    image_path = normalize_image_path(match.group(1))
    remainder = (match.group(2) or "").strip()
    return image_path, remainder


def resolve_award_from_src(src: str, title: str = "") -> Dict[str, str]:
    awards = build_awards_index()
    basename = os.path.basename((src or "").replace("\\", "/").split("?")[0])
    entry = awards["by_filename"].get(basename.lower())
    if not entry and title:
        entry = awards["by_name"].get(title.strip().lower())
    if entry:
        return {
            "name": entry["name"],
            "title": title or entry["name"],
            "src": entry["image"],
            "filename": entry["filename"],
        }
    return {
        "name": title or basename,
        "title": title or basename,
        "src": normalize_image_path(src),
        "filename": basename,
    }


def resolve_usergroup(*, user_rank: Optional[str] = None, user_title: Optional[str] = None, group_image: Optional[str] = None) -> Optional[Dict[str, Any]]:
    index = build_usergroups_index()
    if group_image:
        key = group_image.replace("\\", "/").lower().lstrip("/")
        if key in index["by_image"]:
            return index["by_image"][key]
        base = os.path.basename(key)
        if base in index["by_image"]:
            return index["by_image"][base]
    if user_rank:
        key = user_rank.strip().lower()
        if key in index["by_title"]:
            return index["by_title"][key]
    if user_title:
        key = user_title.strip().lower()
        if key in index["by_usertitle"]:
            return index["by_usertitle"][key]
        if key in index["by_title"]:
            return index["by_title"][key]
    return None


def render_stars_html(count: int, starimage: str = "images/star.png") -> str:
    if count <= 0:
        return ""
    src = normalize_image_path(starimage)
    star_src = src.replace("/static/images/", "images/") if "/static/" in src else starimage
    web_src = normalize_image_path(star_src)
    return "".join(f'<img src="{web_src}" border="0" alt="*" />' for _ in range(count))


def awards_to_html(awards: List[Dict[str, str]]) -> str:
    if not awards:
        return ""
    parts = []
    for award in awards:
        title = award.get("title") or award.get("name") or ""
        src = award.get("src") or ""
        parts.append(f'<a href="#" title="{title}"><img src="{src}" alt="{title}" /></a>')
    return "<br />".join(parts)


if __name__ == "__main__":
    write_index_files()
    print(f"Wrote {AWARDS_JSON}")
    print(f"Wrote {USERGROUPS_JSON}")
