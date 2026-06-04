import re
import ipaddress
from urllib.parse import urlparse
import time
import math
from typing import Dict
from config import RATE_LIMIT_SECONDS

# Simple in-memory rate limiting
user_last_request: Dict[int, float] = {}

# ANSI escape codes regex
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def is_safe_url(url: str) -> bool:
    try:
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname
        if not hostname:
            return False

        # Try to parse hostname as IP
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                return False
        except ValueError:
            # Not an IP, it's a domain name.
            # In a real production app, we might want to resolve it,
            # but for a simple bot, we'll assume domains are generally safe
            # or handled by the HTTP client's safety checks.
            # However, common local names can be blocked.
            if hostname.lower() in ['localhost', '0.0.0.0']:
                return False

        return True
    except Exception:
        return False

def check_rate_limit(user_id: int) -> bool:
    current_time = time.time()
    if user_id in user_last_request:
        if current_time - user_last_request[user_id] < RATE_LIMIT_SECONDS:
            return False
    user_last_request[user_id] = current_time
    return True

def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes from a string."""
    return ANSI_ESCAPE.sub('', text)
