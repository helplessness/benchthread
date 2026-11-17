"""Read-only routes for the existing desktop browser, not an upstream browser."""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

# An admitted action must supply its own evidence, not a generic page heading.
_SEARCH = {
    "bilibili": ("https://search.bilibili.com/all?keyword=", 'a[href*="/video/BV"]', "www.bilibili.com", r"/video/BV[0-9A-Za-z]+/?"),
    "reddit": ("https://www.reddit.com/search/?q=", 'a[href*="/comments/"]', "www.reddit.com", r"/r/[\w]+/comments/[\w]+(?:/[^/?#]+)?/?"),
    "linkedin": ("https://www.linkedin.com/jobs/search/?keywords=", 'a[href*="/jobs/view/"]', "www.linkedin.com", r"/jobs/view/[^/?#]+/?"),
    "xiaohongshu": ("https://www.xiaohongshu.com/search_result?keyword=", 'a[href*="/explore/"], a[href*="/discovery/item/"]', "www.xiaohongshu.com", r"/(?:explore|discovery/item)/[0-9a-f]{24}/?"),
    "boss": ("https://www.zhipin.com/web/geek/jobs?query=", 'a[href*="/job_detail/"]', "www.zhipin.com", r"/job_detail/[^/?#]+\.html"),
    "x": ("https://x.com/search?src=typed_query&q=", 'article[data-testid="tweet"] a[href*="/status/"]', "x.com", r"/[A-Za-z0-9_]{1,15}/status/[0-9]+"),
    "facebook": ("https://www.facebook.com/search/pages/?q=", 'div[role="main"] a[role="link"]', "www.facebook.com", r"/(?!search/|login|help|watch|marketplace|groups/)[A-Za-z0-9.\-_]+/?"),
    "instagram": ("https://www.instagram.com/explore/search/keyword/?q=", 'main a[role="link"]', "www.instagram.com", r"/(?!accounts/|explore/|reels?/|direct/|p/)[A-Za-z0-9_.]{1,30}/"),
}
_REDDIT_POST = r"/r/[\w]+/comments/[a-z0-9]+(?:/[^/?#]+)?/?"
_DETAIL = {
    ("reddit", "read"): ("www.reddit.com", _REDDIT_POST, "shreddit-post h1, h1[slot='title']", "shreddit-post [slot='text-body'], shreddit-post [slot='post-media-container']"),
    ("reddit", "comments"): ("www.reddit.com", _REDDIT_POST, "shreddit-comment [slot='comment']", ""),
    ("linkedin", "read"): ("www.linkedin.com", r"/jobs/view/[^/?#]+/?", "h1.top-card-layout__title, h1.t-24", ".show-more-less-html__markup, #job-details"),
    ("boss", "read"): ("www.zhipin.com", r"/job_detail/[^/?#]+\.html", ".job-banner .name h1", ".job-sec-text"),
    ("xiaohongshu", "read"): ("www.xiaohongshu.com", r"/(?:explore|discovery/item)/[0-9a-f]{24}/?", "#detail-title", "#detail-desc"),
    ("xiaohongshu", "comments"): ("www.xiaohongshu.com", r"/(?:explore|discovery/item)/[0-9a-f]{24}/?", ".comments-container .comment-item .content", ""),
    ("x", "profile"): ("x.com", r"/[A-Za-z0-9_]{1,15}/?", '[data-testid="UserName"]', '[data-testid="UserDescription"]'),
    ("x", "thread"): ("x.com", r"/[A-Za-z0-9_]{1,15}/status/[0-9]+/?", 'article[data-testid="tweet"] [data-testid="tweetText"]', ""),
    ("facebook", "profile"): ("www.facebook.com", r"/(?!search|login|help|watch|marketplace)[A-Za-z0-9.\-_]+/?", 'div[role="main"] h1', 'div[role="main"] [data-pagelet*="ProfileTile"]'),
    ("instagram", "profile"): ("www.instagram.com", r"/(?!accounts|explore|direct)[A-Za-z0-9_.]{1,30}/?", 'main header h1, main header h2', 'main header section'),
}
CHANNEL_READS = {(channel, "search") for channel in _SEARCH} | set(_DETAIL) | {
    ("reddit", "subreddit"), ("x", "posts"), ("instagram", "posts"),
    ("xueqiu", "hot_posts"), ("xueqiu", "hot_stocks"), ("bilibili", "subtitle"),
}
EXPERIMENTAL_CHANNELS = {"x", "facebook", "instagram"}
LOGIN_URLS = {
    "bilibili": "https://passport.bilibili.com/login",
    "reddit": "https://www.reddit.com/login/",
    "linkedin": "https://www.linkedin.com/login",
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
    "boss": "https://www.zhipin.com/web/user/?ka=header-login",
    "x": "https://x.com/i/flow/login",
    "facebook": "https://www.facebook.com/login/",
    "instagram": "https://www.instagram.com/accounts/login/",
    "xueqiu": "https://xueqiu.com/",
}


def channel_read(channel: str, action: str, value: str, limit: int) -> tuple[str, dict]:
    if (channel, action) not in CHANNEL_READS or not 1 <= limit <= 20:
        raise ValueError("CHANNEL_ACTION_NOT_ALLOWED")
    value = value.strip()
    if (channel, action) == ("bilibili", "subtitle"):
        match = re.fullmatch(r"(?:https://www\.bilibili\.com/video/)?(BV[A-Za-z0-9]{10})/?", value)
        if not match:
            raise ValueError("BAD_INPUT")
        bvid = match[1]
        return f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", {
            "json_kind": "bilibili_subtitle", "bvid": bvid, "limit":limit, "login_url":LOGIN_URLS[channel],
        }
    if channel == "xueqiu":
        if value:
            raise ValueError("BAD_INPUT")
        url = (f"https://xueqiu.com/v4/statuses/public_timeline_by_category.json?since_id=-1&max_id=-1&count={limit}&category=-1"
               if action == "hot_posts" else f"https://stock.xueqiu.com/v5/stock/hot_stock/list.json?size={limit}&type=10")
        return url, {"json_kind": action, "limit": limit, "login_url": LOGIN_URLS[channel]}
    if not value or len(value) > 400 or any(ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("BAD_INPUT")
    if action == "search":
        prefix, selector, host, path = _SEARCH[channel]
        rule = {"selector": selector, "link_host": host, "link_path": path, "limit": limit}
        if channel == "x":
            rule["tweet"] = True
        return prefix + quote(value, safe=""), rule
    if action == "posts":
        host, path, _, _ = _DETAIL[channel, "profile"]
        target = urlsplit(value)
        if target.scheme != "https" or target.netloc.lower() != host or not re.fullmatch(path, target.path):
            raise ValueError("BAD_INPUT")
        selector = 'main a[href*="/p/"], main a[href*="/reel/"]'
        item_path = r"/(?:p|reel)/[A-Za-z0-9_-]+/?"
        if channel == "x":
            selector = 'article[data-testid="tweet"] a[href*="/status/"]'
            item_path = rf"/{re.escape(target.path.strip('/'))}/status/[0-9]+"
        return f"https://{host}{target.path}", {"selector":selector, "link_host":host, "link_path":item_path,
                                               "limit":limit, "tweet":channel == "x"}
    if action == "subreddit":
        name = value.removeprefix("r/")
        if not re.fullmatch(r"[\w]{2,21}", name, re.ASCII):
            raise ValueError("BAD_INPUT")
        return f"https://www.reddit.com/r/{name}/", {
            "selector": f'a[href*="/r/{name}/comments/" i]', "link_host": "www.reddit.com",
            "link_path": _REDDIT_POST, "limit": limit,
        }
    host, path, selector, body = _DETAIL[channel, action]
    target = urlsplit(value)
    if target.scheme != "https" or target.netloc.lower() != host or not re.fullmatch(path, target.path):
        raise ValueError("BAD_INPUT")
    return f"https://{host}{target.path}", {"selector": selector, "body": body, "limit": limit if action in {"comments", "thread"} else 1}
