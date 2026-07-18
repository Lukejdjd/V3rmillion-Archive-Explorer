import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT_DIR, "data", "index")
AWARDS_JSON = os.path.join(INDEX_DIR, "awards.json")
USERGROUPS_JSON = os.path.join(INDEX_DIR, "usergroups.json")
SMILIES_JSON = os.path.join(INDEX_DIR, "smilies.json")

with open(AWARDS_JSON, encoding="utf8") as f:
    AWARDS_INDEX = json.load(f)

with open(USERGROUPS_JSON, encoding="utf8") as f:
    USERGROUPS_INDEX = json.load(f)

with open(SMILIES_JSON, encoding="utf8") as f:
    SMILIES_INDEX = json.load(f)

def _build_fuzzy_index(lookup: dict) -> dict:
    """Expand a lookup dict so plural/variant keys also resolve correctly."""
    expanded = dict(lookup)
    for k, v in lookup.items():
        # add 's' suffix variant e.g. "owner" -> "owners"
        if k + 's' not in expanded:
            expanded[k + 's'] = v
        # add without 's' suffix e.g. "owners" -> "owner"  
        if k.endswith('s') and k[:-1] not in expanded:
            expanded[k[:-1]] = v
    return expanded

# awards
AWARDS_BY_FILENAME = AWARDS_INDEX["by_filename"]
AWARDS_BY_NAME = AWARDS_INDEX["by_name"]

# usergroups
USERGROUPS_BY_GID = USERGROUPS_INDEX["by_gid"]
USERGROUPS_BY_IMAGE = USERGROUPS_INDEX["by_image"]
USERGROUPS_BY_TITLE = USERGROUPS_INDEX["by_title"]
USERGROUPS_BY_USERTITLE = USERGROUPS_INDEX["by_usertitle"]
USERGROUPS_BY_TITLE_FUZZY     = _build_fuzzy_index(USERGROUPS_BY_TITLE)

# smilies
SMILIES_BY_FILENAME = SMILIES_INDEX["by_filename"]
SMILIES_BY_SHORTCODE = SMILIES_INDEX["by_shortcode"]
SMILIES_BY_NAME = SMILIES_INDEX["by_name"]

IMAGE_EXT_RE = re.compile(r"\.(png|gif|jpe?g|webp|ico|svg)$", re.I)
GLUED_IMG_TEXT_RE = re.compile(
    r"^((?:https?://(?:[\w.-]+\.)?v3rmillion\.net)?/?images/[\w./\\-]+\.(?:png|gif|jpe?g|webp|ico|svg))(.+)?$",
    re.IGNORECASE,
)

# (name, description, image path)
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

# (gid, title, description, usertitle, namestyle, image path, stars)
USERGROUPS_DATA: List[Dict[str, Any]] = [
    {"gid": 1, "title": "Guests", "description": "The default group that all visitors are assigned to unless they're logged in.", "usertitle": "Unregistered", "namestyle": "{username}", "image": "", "stars": 0},
    {"gid": 2, "title": "Members", "description": "After registration, all users are placed in this group by default.", "usertitle": "", "namestyle": '<span style="color: #CD1818 ;"><strong>{username}</strong></span>', "image": "", "stars": 1},
    {"gid": 3, "title": "Head Moderators", "description": "These users can moderate any forum.", "usertitle": "Head Moderators", "namestyle": '<span style="color: #0099FF; text-shadow: 1px 1px 6px #8F8F8F;"><bold>{username}</bold></span>', "image": "images/UserBars/Redesigned/Head_Mod.png", "stars": 6},
    {"gid": 4, "title": "Administrators", "description": "The group all administrators belong to.", "usertitle": "Administrator", "namestyle": '<span style="color: #B40404;text-shadow: 0px 3px 6px rgba(188, 150, 150, 0.48); font-weight:bold;">{username}</span>', "image": "images/UserBars/Redesigned/Administrator.png", "stars": 7},
    {"gid": 5, "title": "Account not Activated", "description": "Users that have not activated their account by email or manually been activated yet.", "usertitle": "Account not Activated", "namestyle": '<span style="color: #BFBFBF;"><strong>{username}</strong></span>', "image": "", "stars": 0},
    {"gid": 6, "title": "Moderators", "description": "These users moderate specific forums.", "usertitle": "Moderator", "namestyle": '<span style="color: #0099FF;"><bold>{username}</bold></span>', "image": "images/UserBars/Redesigned/Moderator.png", "stars": 5},
    {"gid": 7, "title": "Banned", "description": "The default user group to which members that are banned are moved to.", "usertitle": "Banned", "namestyle": '<span style="color: #000000;"><strong><s>{username}</s></strong></span>', "image": "", "stars": 0},
    {"gid": 10, "title": "Vip", "description": "Vip", "usertitle": "", "namestyle": '<span style="color:#29CB09;">{username}</span>', "image": "images/UserBars/Redesigned/VIP.png", "stars": 5},
    {"gid": 11, "title": "Elite", "description": "Elite", "usertitle": "Elite", "namestyle": '<span style="color:#FFC800;"><strong>{username}</strong></span>', "image": "images/UserBars/Redesigned/Elite.png", "stars": 5},
    {"gid": 12, "title": "Synapse", "description": "Synapse Developer", "usertitle": "Synapse", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Redesigned/Synapse.png", "stars": 0},
    {"gid": 13, "title": "Owner", "description": "Owner", "usertitle": "", "namestyle": '<span style="color: #B40404;text-shadow: 0px 3px 6px rgba(188, 150, 150, 0.48); font-weight:bold;">{username}</span>', "image": "images/UserBars/Redesigned/Owner.png", "stars": 12},
    {"gid": 14, "title": "Esoterica", "description": "Esoterica", "usertitle": "Esoterica", "namestyle": '<span style="color:#c77138;"><strong>{username}</strong></span>', "image": "images/UserBars/Redesigned/Esoterica.png", "stars": 0},
    {"gid": 15, "title": "Advisor", "description": "Advisor", "usertitle": "", "namestyle": '<span style="color: #F763FF;">{username}</span>', "image": "images/UserBars/Redesigned/Advisor.png", "stars": 0},
    {"gid": 16, "title": "Vermillion Missionary", "description": "Vermillion Missionary", "usertitle": "Vermillion Missionary", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/V3rmillionMissionary_Dec.png", "stars": 0},
    {"gid": 18, "title": "Renzi", "description": "Renzi", "usertitle": "Renzi", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/Renzi.png", "stars": 2},
    {"gid": 19, "title": "Scriptware", "description": "Scriptware", "usertitle": "Scriptware", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Redesigned/Scriptware.png", "stars": 0},
    {"gid": 20, "title": "Nihon", "description": "Nihon", "usertitle": "Nihon", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/nihonug.png", "stars": 0},
    {"gid": 21, "title": "Celery", "description": "Celery", "usertitle": "Celery", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/celeryug.png", "stars": 0},
    {"gid": 22, "title": "Robot", "description": "Robot", "usertitle": "", "namestyle": "{username}", "image": "", "stars": 0},
    {"gid": 23, "title": "Developer", "description": "", "usertitle": "", "namestyle": '<span style="color: #04B485;text-shadow: 0px 3px 6px rgba(129, 173, 161, 0.48); font-weight:bold;">{username}</span>', "image": "images/UserBars/Redesigned/Developer.png", "stars": 5},
    {"gid": 24, "title": "Rain", "description": "Rain", "usertitle": "Rain", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/Rain.png", "stars": 0},
    {"gid": 25, "title": "Alvaria", "description": "Alvaria", "usertitle": "Alvaria", "namestyle": '<span style="color: #FFFFF1;">{username}</span>', "image": "images/UserBars/Custom/Alvaria.png", "stars": 0},
]

# (name, find, image path)
SMILIES_DATA: List[Tuple[str, str, str]] = [
    ('Angel', ':angel:', 'images/smilies/angel.png'),
    ('Angry', ':angry:', 'images/smilies/angry.png'),
    ('Arrow', ':arrow:', 'images/smilies/arrow.png'),
    ('Biggrin', ':biggrin:', 'images/smilies/biggrin.png'),
    ('Blush', ':blush:', 'images/smilies/blush.png'),
    ('Confused', ':confused:', 'images/smilies/confused.png'),
    ('Cool', ':cool:', 'images/smilies/cool.png'),
    ('Cry', ':cry:', 'images/smilies/cry.png'),
    ('Dab', ':dab:', 'images/smilies/dab.png'),
    ('Dodgy', ':dodgy:', 'images/smilies/dodgy.png'),
    ('Drugs', ':drugs:', 'images/smilies/drugs.png'),
    ('Drunk', ':drunk:', 'images/smilies/drunk.png'),
    ('Exclamation', ':exclamation:', 'images/smilies/exclamation.png'),
    ('Fury', ':fury:', 'images/smilies/fury.png'),
    ('Heart', ':heart:', 'images/smilies/heart.png'),
    ('Huh', ':huh:', 'images/smilies/huh.png'),
    ('Idea', ':idea:', 'images/smilies/idea.png'),
    ('RaysA', ':raysA:', 'images/smilies/raysA.png'),
    ('RaysDisappoint', ':raysDisappoint:', 'images/smilies/raysDisappoint.png'),
    ('RaysFab', ':raysFab:', 'images/smilies/raysFab.png'),
    ('RaysHapp', ':raysHapp:', 'images/smilies/raysHapp.png'),
    ('RaysHehe', ':raysHehe:', 'images/smilies/raysHehe.png'),
    ('RaysHello', ':raysHello:', 'images/smilies/raysHello.png'),
    ('RaysHmf', ':raysHmf:', 'images/smilies/raysHmf.png'),
    ('RaysHmM', ':raysHmM:', 'images/smilies/raysHmM.png'),
    ('RaysKawaii', ':raysKawaii:', 'images/smilies/raysKawaii.png'),
    ('RaysLaugh', ':raysLaugh:', 'images/smilies/raysLaugh.png'),
    ('RaysLick', ':raysLick:', 'images/smilies/raysLick.png'),
    ('RaysLove', ':raysLove:', 'images/smilies/raysLove.png'),
    ('RaysLurk', ':raysLurk:', 'images/smilies/raysLurk.png'),
    ('RaysOkpal', ':raysOkpal:', 'images/smilies/raysOkpal.png'),
    ('RaysPanic', ':raysPanic:', 'images/smilies/raysPanic.png'),
    ('RaysParty', ':raysParty:', 'images/smilies/raysParty.png'),
    ('RaysPog', ':raysPog:', 'images/smilies/raysPog.png'),
    ('RaysPure', ':raysPure:', 'images/smilies/raysPure.png'),
    ('RaysQuizzical', ':raysQuizzical:', 'images/smilies/raysQuizzical.png'),
    ('RaysSad', ':raysSad:', 'images/smilies/raysSad.png'),
    ('RaysShock', ':raysShock:', 'images/smilies/raysShock.png'),
    ('RaysShrug', ':raysShrug:', 'images/smilies/raysShrug.png'),
    ('RaysShy', ':raysShy:', 'images/smilies/raysShy.png'),
    ('RaysSun', ':raysSun:', 'images/smilies/raysSun.png'),
    ('RaysTable', ':raysTable:', 'images/smilies/raysTable.gif'),
    ('RaysUpset', ':raysUpset:', 'images/smilies/raysUpset.png'),
    ('RaysYes', ':raysYes:', 'images/smilies/raysYes.png'),
    ('RaysZzz', ':raysZzz:', 'images/smilies/raysZzz.png'),
    ('Rolleyes', ':rolleyes:', 'images/smilies/rolleyes.png'),
    ('Sad', ':sad:', 'images/smilies/sad.png'),
    ('Shy', ':shy:', 'images/smilies/shy.png'),
    ('Sleepy', ':sleepy:', 'images/smilies/sleepy.png'),
    ('Smile', ':smile:', 'images/smilies/smile.png'),
    ('Thinking', ':thinking:', 'images/smilies/thinking.png'),
    ('Thomas', ':thomas:', 'images/smilies/thomas.png'),
    ('Tongue', ':tongue:', 'images/smilies/tongue.png'),
    ('Undecided', ':undecided:', 'images/smilies/undecided.png'),
    ('Watching', ':watching:', 'images/smilies/watching.png'),
    ('Wink', ':wink:', 'images/smilies/wink.png'),
]

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
        "by_filename": by_filename,
        "by_name": by_name,
    }


def build_usergroups_index() -> Dict[str, Any]:
    groups = []

    for g in USERGROUPS_DATA:
        group = dict(g)  # copy so original data isn't mutated

        if group.get("image"):
            group["image"] = _sql_image_to_web(group["image"])

        groups.append(group)

    by_gid = {str(g["gid"]): g for g in groups}

    by_image: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    by_usertitle: Dict[str, Dict[str, Any]] = {}

    for group in groups:
        if group.get("image"):
            key = group["image"].replace("\\", "/").lower()
            by_image[key] = group
            by_image[os.path.basename(key)] = group

        by_title[group["title"].lower()] = group

        if group.get("usertitle"):
            by_usertitle[group["usertitle"].lower()] = group

    return {
        "by_gid": by_gid,
        "by_image": by_image,
        "by_title": by_title,
        "by_usertitle": by_usertitle,
        "groups": groups,
    }


def build_smilies_index() -> Dict[str, Any]:
    by_shortcode: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}
    by_filename: Dict[str, Dict[str, str]] = {}
    
    for name, shortcode, image_path in SMILIES_DATA:
        filename = os.path.basename(image_path)
        
        entry = {
            "name": name.strip(),
            "shortcode": shortcode.strip(),
            "image": _sql_image_to_web(image_path),
        }
        by_shortcode[shortcode.strip().lower()] = entry
        by_name[name.strip().lower()] = entry
        by_filename[filename.lower()] = entry
        
    return {
        "by_shortcode": by_shortcode,
        "by_name": by_name,
        "by_filename": by_filename,
    }

def write_index_files() -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(AWARDS_JSON, "w", encoding="utf-8") as f:
        json.dump(build_awards_index(), f, ensure_ascii=False, indent=2)
    with open(USERGROUPS_JSON, "w", encoding="utf-8") as f:
        json.dump(build_usergroups_index(), f, ensure_ascii=False, indent=2)
    with open(SMILIES_JSON, "w", encoding="utf-8") as f:
        json.dump(build_smilies_index(), f, ensure_ascii=False, indent=2)


def normalize_image_path(src: str) -> str:
    if not src:
        return src

    src = src.strip().replace("\\", "/")

    def decode_wrapped_url(value: str) -> str:
        for _ in range(3):
            decoded = urllib.parse.unquote(value)
            if decoded == value:
                break
            value = decoded
        return value

    src = re.sub(
        r"^https?://(?:www\.)?v3rmillion\.net",
        "",
        src,
        flags=re.I
    )

    if src.startswith("images/"):
        src = "/" + src

    # Some archived MyBB image URLs are stored as /images/<external-url>.
    # Those are not local assets; preserve the real external image URL.
    wrapped_external = re.match(r"^/images/(https?://.+)$", src, flags=re.I)
    if wrapped_external:
        src = wrapped_external.group(1)

    parsed = urllib.parse.urlparse(src)
    if parsed.hostname and parsed.hostname.lower().endswith("duckduckgo.com"):
        qs = urllib.parse.parse_qs(parsed.query)
        wrapped_url = (qs.get("u") or qs.get("url") or [""])[0]
        wrapped_url = decode_wrapped_url(wrapped_url)
        if wrapped_url.startswith(("http://", "https://")):
            return wrapped_url

    basename = os.path.basename(src.split("?")[0]).lower()

    # -------- smilies ----------
    smiley = SMILIES_BY_FILENAME.get(basename)

    if smiley:
        return smiley["image"]

    # -------- usergroups ----------
    usergroup = USERGROUPS_BY_IMAGE.get(basename)

    if usergroup:
        return usergroup["image"]

    # -------- awards ----------
    award = AWARDS_BY_FILENAME.get(basename)

    if award:
        return award["image"]

    # -------- generic images ----------
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


def resolve_award(src: str, name: str = "") -> Dict[str, str]:
    basename = os.path.basename((src or "").replace("\\", "/").split("?")[0]).lower()

    entry = AWARDS_BY_FILENAME.get(basename)

    if not entry and name:
        entry = AWARDS_BY_NAME.get(name.strip().lower())

    if entry:
        return {
            "name": entry["name"],
            "description": entry.get("description"),
        }

    return {
        "name": name or basename,
        "description": None,
    }


def resolve_usergroup(image_src: str, user_rank: str = "") -> Dict[str, str]:
    basename = os.path.basename((image_src or "").replace("\\", "/").split("?")[0]).lower()

    entry = USERGROUPS_BY_IMAGE.get(basename)

    if not entry and user_rank:
        entry = USERGROUPS_BY_TITLE_FUZZY.get(user_rank.strip().lower())

    if entry:
        return {
            "title": entry["title"],
            "description": entry["description"],
            "usertitle": entry.get("usertitle"),
        }

    return {
        "title": user_rank or basename,
        "description": None,
        "usertitle": None,
    }


if __name__ == "__main__":
    write_index_files()
    print(f"Wrote {AWARDS_JSON}")
    print(f"Wrote {USERGROUPS_JSON}")
    print(f"Wrote {SMILIES_JSON}")
