# scraper.py
import requests
from bs4 import BeautifulSoup

def extract_listing_content(url: str) -> str:
    """抓取并提取房源页面的核心文本信息"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=12)
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"网页抓取失败: {str(e)}")

    soup = BeautifulSoup(response.text, "html.parser")

    # 移除无用的脚本、样式和页眉页脚
    for element in soup(["script", "style", "header", "footer", "nav"]):
        element.decompose()

    # 提取正文文本并合并多余空白
    lines = (line.strip() for line in soup.get_text().splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    cleaned_text = "\n".join(chunk for chunk in chunks if chunk)

    # 截取前 8000 个字符防止超出 token 限制
    return cleaned_text[:8000]