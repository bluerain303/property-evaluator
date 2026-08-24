import json
import os

ROOT = os.path.dirname(__file__)
PROMPTS_FILE = os.path.join(ROOT, "prompts.json")


def load_prompts():
    try:
        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except FileNotFoundError:
        return {}
    except Exception:
        # 防守：如果文件格式错误或读取失败，返回空字典
        return {}


PROMPTS = load_prompts()


def list_prompt_names():
    """返回按插入顺序的 prompt 名称列表（如果可用）。"""
    return list(PROMPTS.keys())


def get_prompt(name: str):
    return PROMPTS.get(name)


def default_prompt_name():
    names = list_prompt_names()
    return names[0] if names else None


def save_prompt(name: str, content: str, overwrite: bool = False) -> (bool, str):
    """保存或更新一个 prompt 到 prompts.json。

    返回 (success: bool, message: str)。
    如果同名 prompt 已存在且 overwrite 为 False，则不会覆盖并返回 False。
    写入采用临时文件 + 原子替换以避免损坏文件。
    """
    if not name or not name.strip():
        return False, "Prompt 名称不能为空。"
    name = name.strip()
    if not isinstance(content, str) or not content.strip():
        return False, "Prompt 内容不能为空。"

    exists = name in PROMPTS
    if exists and not overwrite:
        return False, f"名为 '{name}' 的 prompt 已存在；如需覆盖请勾选覆盖选项。"

    # 更新内存结构（保持插入顺序：如果是新键则按顺序添加）
    PROMPTS[name] = content

    # 将 PROMPTS 写回文件（原子写入）
    tmp_path = PROMPTS_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(PROMPTS, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, PROMPTS_FILE)
        return True, f"Prompt '{name}' 已保存。"
    except Exception as e:
        # 尝试移除临时文件（忽略错误）
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False, f"保存失败: {e}"
