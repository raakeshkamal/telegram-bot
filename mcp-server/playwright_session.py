from curl_cffi import requests


def fetch_html_sync(url: str, timeout: int = 30) -> str:
    response = requests.get(url, impersonate="chrome124", timeout=timeout)
    response.raise_for_status()
    return response.text
