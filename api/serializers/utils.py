# api/serializers/utils.py

def extract_youtube_id(url):
    if not url:
        return None
    import urllib.parse as urlparse
    parsed = urlparse.urlparse(url)
    if parsed.hostname == 'youtu.be':
        return parsed.path[1:]
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            p = urlparse.parse_qs(parsed.query)
            return p.get('v', [None])[0]
        if parsed.path.startswith(('/embed/', '/shorts/')):
            parts = parsed.path.split('/')
            if len(parts) > 2:
                return parts[2]
    return None
