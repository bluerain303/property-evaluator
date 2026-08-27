# llm_service.py
import os
import litellm
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = "你是一个通用的评估助手。请根据提供的信息生成评估报告。"


def evaluate_property(model_name: str, listing_text: str, custom_api_key: str = None, system_prompt: str = None) -> str:
    """通用模型调用函数

    如果传入 system_prompt（非空字符串），则使用该 prompt；否则回退到模块内置的 SYSTEM_PROMPT。
    """
    prompt_to_use = system_prompt.strip() if system_prompt and system_prompt.strip() else SYSTEM_PROMPT

    messages = [
        {"role": "system", "content": prompt_to_use},
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