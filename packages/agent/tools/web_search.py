"""Agent 工具 web_search — Phase O #91

外部网页检索；默认 mock 模式（CI 无 Key），可切换 HTTP 搜索 API。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from html import unescape
from typing import Any
from urllib.parse import unquote

import httpx

from packages.agent.tool_envelope import success_envelope

logger = logging.getLogger("ai_platform.agent.tools.web_search")

_VALID_MODES = frozenset({"mock", "http", "ddg"})
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_USER_AGENT = "Mozilla/5.0"
_OPEN_METEO_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_WEATHER_QUERY_RE = re.compile(
    r"天气|气温|温度|预报|下雨|降雪|冷暖|weather|forecast|temperature",
    re.IGNORECASE,
)
_WEATHER_NOISE_RE = re.compile(
    r"(今天|明天|后天|大后天|本周|这周|实时|当前|现在|最新|怎么样|如何|查询|搜索|搜一下|搜|查一下|查|帮我|帮忙|看下|看看|告诉我|一下|的|呢|吗|啊|呀|吧|weather|forecast|temperature)",
    re.IGNORECASE,
)

# WMO weather code → 中文简述（Open-Meteo）
_WMO_CONDITION_ZH: dict[int, str] = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "大阵雨",
    85: "小阵雪",
    86: "大阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}


def _clamp_top_k(value: Any, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))


def normalize_search_results(raw: Any, *, top_k: int) -> list[dict[str, str]]:
    """将 HTTP 响应规范为 [{title, snippet, url}, ...]。"""
    items: list[Any]
    if isinstance(raw, dict):
        candidate = raw.get("results") or raw.get("items") or raw.get("data")
        items = candidate if isinstance(candidate, list) else []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        snippet = str(item.get("snippet") or item.get("description") or item.get("body") or "").strip()
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        if not title and not snippet and not url:
            continue
        out.append(
            {
                "title": title or "(no title)",
                "snippet": snippet or "",
                "url": url or "",
            }
        )
        if len(out) >= top_k:
            break
    return out


def mock_web_search(query: str, *, top_k: int) -> list[dict[str, str]]:
    """确定性 mock 结果，便于单测与 demo。"""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    templates = [
        {
            "title": f"Mock: {query} — overview",
            "snippet": f"[mock] Top summary for '{query}'. Source id={digest}.",
            "url": f"https://example.com/search/{digest}/1",
        },
        {
            "title": f"Mock: {query} — guide",
            "snippet": f"[mock] Practical guide related to '{query}'.",
            "url": f"https://example.com/search/{digest}/2",
        },
        {
            "title": f"Mock: {query} — news",
            "snippet": f"[mock] Recent discussion about '{query}'.",
            "url": f"https://example.com/search/{digest}/3",
        },
        {
            "title": f"Mock: {query} — reference",
            "snippet": f"[mock] Reference material for '{query}'.",
            "url": f"https://example.com/search/{digest}/4",
        },
        {
            "title": f"Mock: {query} — forum",
            "snippet": f"[mock] Community thread mentioning '{query}'.",
            "url": f"https://example.com/search/{digest}/5",
        },
    ]
    return templates[:top_k]


def _clean_html_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _decode_ddg_href(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if "uddg=" in href:
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            return unquote(match.group(1))
    if href.startswith("//"):
        return f"https:{href}"
    return href


def parse_ddg_html(html: str, *, top_k: int) -> list[dict[str, str]]:
    """解析 DuckDuckGo HTML 结果页。"""
    pattern = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
        re.DOTALL | re.IGNORECASE,
    )
    out: list[dict[str, str]] = []
    for href, title_raw, snippet_raw in pattern.findall(html):
        title = _clean_html_text(title_raw)
        snippet = _clean_html_text(snippet_raw)
        url = _decode_ddg_href(href)
        if not title and not snippet:
            continue
        out.append(
            {
                "title": title or "(no title)",
                "snippet": snippet,
                "url": url,
            }
        )
        if len(out) >= top_k:
            break
    return out


async def ddg_web_search(
    query: str,
    *,
    top_k: int,
    timeout_seconds: float,
) -> list[dict[str, str]]:
    headers = {
        "User-Agent": _DDG_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    data = {"q": query, "b": "", "kl": "cn-zh"}
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        resp = await client.post(_DDG_HTML_URL, data=data, headers=headers)
        resp.raise_for_status()
    if resp.status_code != 200:
        raise RuntimeError(f"DuckDuckGo 返回 HTTP {resp.status_code}")
    results = parse_ddg_html(resp.text, top_k=top_k)
    if not results:
        raise RuntimeError("DuckDuckGo 未返回可解析结果（可能被网络拦截）")
    return results


def is_weather_query(query: str) -> bool:
    return bool(_WEATHER_QUERY_RE.search(query or ""))


def extract_weather_location(query: str) -> str:
    """从天气类搜索词中提取地名。"""
    text = (query or "").strip()
    text = _WEATHER_NOISE_RE.sub(" ", text)
    text = _WEATHER_QUERY_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,、")
    return text or query.strip()


def wmo_code_to_zh(code: Any) -> str:
    try:
        return _WMO_CONDITION_ZH.get(int(code), f"天气码 {int(code)}")
    except (TypeError, ValueError):
        return "未知"


def build_weather_summary(weather: dict[str, Any]) -> str:
    loc = weather.get("location") or "当地"
    region = weather.get("region") or ""
    temp = weather.get("temperature_c")
    feels = weather.get("feels_like_c")
    humidity = weather.get("humidity_percent")
    wind = weather.get("wind_speed_kmh")
    condition = weather.get("condition") or "未知"
    observed = weather.get("observed_at") or ""
    place = f"{loc}（{region}）" if region else loc
    parts = [f"{place}当前 {temp}°C，{condition}"]
    if feels is not None:
        parts.append(f"体感 {feels}°C")
    if humidity is not None:
        parts.append(f"湿度 {humidity}%")
    if wind is not None:
        parts.append(f"风速 {wind} km/h")
    if observed:
        parts.append(f"观测时间 {observed}")
    parts.append("数据来源 Open-Meteo")
    return "，".join(parts) + "。"


async def fetch_open_meteo_weather(
    location: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """通过 Open-Meteo 获取实时天气（免费、无需 API Key）。"""
    loc = (location or "").strip()
    if not loc:
        return None

    headers = {"User-Agent": _DDG_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        geo_resp = await client.get(
            _OPEN_METEO_GEO_URL,
            params={"name": loc, "count": 1, "language": "zh"},
            headers=headers,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        hits = geo_data.get("results") if isinstance(geo_data, dict) else None
        if not isinstance(hits, list) or not hits:
            return None
        hit = hits[0]
        lat = hit.get("latitude")
        lon = hit.get("longitude")
        if lat is None or lon is None:
            return None

        wx_resp = await client.get(
            _OPEN_METEO_FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "weather_code,wind_speed_10m,wind_direction_10m"
                ),
                "timezone": hit.get("timezone") or "Asia/Shanghai",
            },
            headers=headers,
        )
        wx_resp.raise_for_status()
        wx_data = wx_resp.json()

    current = wx_data.get("current") if isinstance(wx_data, dict) else None
    if not isinstance(current, dict):
        return None

    name = str(hit.get("name") or loc).strip()
    admin1 = str(hit.get("admin1") or "").strip()
    country = str(hit.get("country") or "").strip()
    region_parts = [p for p in (admin1, country) if p]
    condition = wmo_code_to_zh(current.get("weather_code"))

    weather: dict[str, Any] = {
        "location": name,
        "region": "，".join(region_parts),
        "latitude": lat,
        "longitude": lon,
        "observed_at": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "condition": condition,
        "source": "open-meteo",
    }
    weather["summary"] = build_weather_summary(weather)
    return weather


def prepend_weather_result(
    results: list[dict[str, str]],
    weather: dict[str, Any],
) -> list[dict[str, str]]:
    """把实时天气摘要插入搜索结果首位，便于 LLM 直接引用数值。"""
    summary = str(weather.get("summary") or "").strip()
    if not summary:
        return results
    loc = weather.get("location") or "当地"
    enriched = [
        {
            "title": f"{loc} 实时天气（Open-Meteo）",
            "snippet": summary,
            "url": "https://open-meteo.com/",
        },
        *results,
    ]
    return enriched


async def http_web_search(
    query: str,
    *,
    top_k: int,
    url: str,
    timeout_seconds: float,
) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url, json={"query": query, "top_k": top_k})
        resp.raise_for_status()
        if "json" in resp.headers.get("content-type", ""):
            return normalize_search_results(resp.json(), top_k=top_k)
        return normalize_search_results(json.loads(resp.text), top_k=top_k)


async def handle_web_search(arguments: dict[str, Any]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)

    from apps.gateway.settings import get_settings

    settings = get_settings()
    top_k = _clamp_top_k(
        arguments.get("top_k"),
        default=settings.web_search_top_k,
        maximum=settings.web_search_max_top_k,
    )
    mode = (settings.web_search_mode or "mock").strip().lower()
    if mode not in _VALID_MODES:
        mode = "mock"

    results: list[dict[str, str]]
    mode_used = mode
    try:
        if mode == "ddg":
            results = await ddg_web_search(
                query.strip(),
                top_k=top_k,
                timeout_seconds=settings.web_search_timeout_seconds,
            )
        elif mode == "http" and (settings.web_search_url or "").strip():
            results = await http_web_search(
                query.strip(),
                top_k=top_k,
                url=settings.web_search_url.strip(),
                timeout_seconds=settings.web_search_timeout_seconds,
            )
        else:
            results = mock_web_search(query.strip(), top_k=top_k)
            mode_used = "mock"
    except Exception as e:
        logger.warning("web_search %s failed: %s", mode, e)
        if mode == "http" and (settings.web_search_url or "").strip():
            results = mock_web_search(query.strip(), top_k=top_k)
            mode_used = "mock_fallback"
        elif mode == "ddg":
            return json.dumps(
                {
                    "error": f"真实搜索失败: {e}",
                    "mode": "ddg_failed",
                    "query": query.strip(),
                },
                ensure_ascii=False,
            )
        else:
            results = mock_web_search(query.strip(), top_k=top_k)
            mode_used = "mock_fallback"

    payload: dict[str, Any] = {
        "tool": "web_search",
        "mode": mode_used,
        "query": query.strip(),
        "results": results,
    }

    if getattr(settings, "web_search_weather_enrich", True) and is_weather_query(query):
        location = extract_weather_location(query)
        try:
            weather = await fetch_open_meteo_weather(
                location,
                timeout_seconds=min(settings.web_search_timeout_seconds, 8.0),
            )
            if weather:
                payload["weather"] = weather
                payload["results"] = prepend_weather_result(results, weather)
        except Exception as e:
            logger.warning("web_search weather enrich failed for %r: %s", location, e)
            payload["weather_error"] = str(e)

    return success_envelope(payload)
