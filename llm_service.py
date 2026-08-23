# llm_service.py
import os
import litellm
from dotenv import load_dotenv

load_dotenv()

# 统一评估 Prompt 模板
SYSTEM_PROMPT = """你是一名精通卢森堡房地产市场的资深置业顾问。
请根据提供的房源页面抓取内容，输出一份清晰、客观、结构化的中文评估报告。

报告需严格按以下维度展开：
1. 🏠 **基本信息速览**：房源类型、所在市镇（Commune）、标价、使用面积、每平米单价、卧室/卫生间数量、车位情况。
2. ⚡ **能效与翻新评估**：
   - 提取能效等级（Passeport Énergétique）。
   - 若能效等级偏低（如 E/F/G/H 级），说明潜在的高昂翻新工程（如外墙外保温、热泵系统、门窗置换）及大致成本预期。
3. 🏫 **家庭与周边配套**：
   - 所在区域的宜居程度。
   - 周边学校资源（针对基础教育、中学生阶段）及托儿机构（Crèche）的分布便利度。
4. 🚗 **通勤与交通**：
   - 前往卢森堡市中心（Kirchberg/Ville-Haute/Cloche d'Or）的早晚高峰通勤预期及公共交通便利性。
   - 前往卢森堡机场（Findel）的通达性。
5. ⚠️ **中介话术与风险提示**：
   - 拆解房源描述中是否有修饰词掩盖缺点（如 "à rafraîchir" 意味着需全面翻新，"calme" 是否位置过偏等）。
   - 列出 3 个核心优势与 3 个潜在硬伤。
6. ⭐️ **综合推荐评分**：给出 1 到 5 星评分及一句话置业建议。
"""

def evaluate_property(model_name: str, listing_text: str, custom_api_key: str = None) -> str:
    """通用模型调用函数"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"以下是从房产网站抓取的房源信息：\n\n{listing_text}"}
    ]
    
    # 动态注入 API Key
    kwargs = {}
    if custom_api_key:
        kwargs["api_key"] = custom_api_key
        
    try:
        response = litellm.completion(
            model=model_name,
            messages=messages,
            temperature=0.3,
            **kwargs
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"AI 生成失败: {str(e)}")