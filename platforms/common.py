import re
from urllib.parse import quote, urljoin


def to_proxy_url(raw_url: str, proxy_path: str) -> str:
    return f"{proxy_path}?url={quote(raw_url, safe='')}"


def rewrite_m3u8_line(line: str, playlist_url: str, proxy_path: str) -> str:
    stripped = line.strip()
    if not stripped:
        return line

    if stripped.startswith("#"):
        def repl(match):
            raw_uri = match.group(1)
            absolute_uri = urljoin(playlist_url, raw_uri)
            return f'URI="{to_proxy_url(absolute_uri, proxy_path)}"'

        return re.sub(r'URI="([^"]+)"', repl, line)

    absolute_segment_url = urljoin(playlist_url, stripped)
    return to_proxy_url(absolute_segment_url, proxy_path)
