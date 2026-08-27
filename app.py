import hmac
import os
import time

import streamlit as st
from scraper import extract_listing_content
from llm_service import evaluate_property
import json
import urllib.parse
from google_sheet_connector import GoogleSheetConnector


# 你的 Google Sheet 链接（替换成实际表格 URL）
SHEET_URL = "https://docs.google.com/spreadsheets/d/1x05RYM38r_vWpE8OE0eCVygzwOIA6jIesBwR5HY9fDk/edit"
# 初始化连接器实例
sheet_db = GoogleSheetConnector(spreadsheet_url=SHEET_URL, worksheet="HistoryV1")

# 你的 Google Sheet 链接（替换成实际表格 URL）
SHEET_URL_PROMPTS = "https://docs.google.com/spreadsheets/d/1tcgcfuo86BCzjmNcwXpuwAXKdbZAAKv6SGZoFe34ddQ/edit"
# 初始化连接器实例
sheet_db_prompts = GoogleSheetConnector(spreadsheet_url=SHEET_URL_PROMPTS, worksheet="Prompts")

def run_with_retry(task_name: str, func, max_retries: int = 2, delay_seconds: float = 1.5):
    """执行任务并在失败时自动重试，保留最终异常详情。"""
    last_error = None

    for attempt in range(1, max_retries + 2):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt > max_retries:
                raise RuntimeError(f"{task_name}失败（已自动重试 {max_retries} 次）: {exc}") from exc

            st.info(f"⚠️ {task_name} 第 {attempt} 次尝试失败，正在自动重试... ({attempt + 1}/{max_retries + 1})")
            time.sleep(delay_seconds)

    raise RuntimeError(f"{task_name}失败: {last_error}")


# 页面基础配置
st.set_page_config(
    page_title="卢森堡房产 AI 智能评估助手",
    page_icon="🏡",
    layout="wide"
)

st.title("🏡 卢森堡房源 AI 智能分析评估")
st.markdown("输入 `athome.lu` / `wortimmo.lu` 等房源链接，一键生成多维度中文深度评估报告。")

# 侧边栏：模型配置
with st.sidebar:

    passcode = st.text_input(
        "访问口令",
        type="password",
        help="请输入访问口令"
    )
    expected_passcode = os.getenv("PASSCODE", "")
    access_granted = bool(expected_passcode) and hmac.compare_digest(passcode, expected_passcode)

    st.header("⚙️ 模型配置")
    
    model_provider = st.selectbox(
        "选择 AI 模型",
        options=[
            "gemini/gemini-3.6-flash",
            "gemini/gemini-1.5-pro",
            "deepseek/deepseek-chat",
            "gpt-4o-mini",
            "gpt-4o"
        ],
        index=0,
        help="支持各主流模型统一接口"
    )

    custom_api_key = st.text_input(
        "API Key (留空则默认读取 .env)",
        type="password",
        disabled=not access_granted,
        help="可临时手动填入对应的 API Key"
    )

    # 选择使用哪个 prompt：自定义或系统默认
    st.markdown("---")
    st.header("⚙️ 系统提示词")
    st.caption("上方为系统默认提示词（只读），下方可编辑自定义提示词。")
    default_choice_index = 1 if st.session_state.get("system_prompt", "") else 0
    prompt_choice = st.radio(
        "选择要使用的 System Prompt：",
        options=["使用系统默认提示词", "使用自定义提示词"],
        index=default_choice_index,
        disabled=not access_granted,
        help="选择后，评估将使用对应的 System Prompt。"
    )

    # 从 Google Sheet 中读取可用的系统 Prompt 列表
    try:
        prompt_df = sheet_db_prompts.read_prompts(ttl=0)
        prompt_map = {
            str(row["Name"]).strip(): str(row["Prompt"])
            for _, row in prompt_df.iterrows()
            if str(row["Name"]).strip() and str(row["Prompt"]).strip()
        }
        prompt_names = list(prompt_map)
    except Exception as ex:
        prompt_map = {}
        prompt_names = []
        st.warning(f"读取 Prompt Google Sheet 失败: {str(ex)}")

    # 如果上一次保存后希望选中某个 prompt（在保存按钮处理处设置），先把它移动到 selected_prompt_name
    if "_select_after_save" in st.session_state:
        st.session_state["selected_prompt_name"] = st.session_state.pop("_select_after_save")

    if "selected_prompt_name" not in st.session_state:
        st.session_state["selected_prompt_name"] = prompt_names[0] if prompt_names else ""

    selected_prompt_name = st.selectbox(
        "选择系统默认 Prompt（下拉）",
        options=prompt_names or ["(无可用提示词，检查 prompts.json)"],
        index=prompt_names.index(st.session_state["selected_prompt_name"]) if prompt_names and st.session_state.get("selected_prompt_name") in prompt_names else 0,
        help="选择一个系统内置的 Prompt，内容会显示在下方只读框中",
        disabled=not access_granted,
        key="selected_prompt_name"
    )

    # 底部：只读显示系统默认 prompt（选中条目）
    default_prompt_content = prompt_map.get(st.session_state.get("selected_prompt_name"), "")
    st.text_area(
        "系统默认 Prompt（只读）",
        value=default_prompt_content,
        height=150,
        disabled=True
    )

    # 可编辑的自定义 system prompt（用户输入并保存到 session_state），放在系统默认框下方
    custom_prompt = st.text_area(
        "自定义 System Prompt（可选）",
        value=st.session_state.get("system_prompt", ""),
        key="system_prompt",
        height=150,
        disabled=not access_granted,
        help="在此输入自定义提示词；选中后将覆盖系统默认提示词。"
    )

    st.subheader("保存当前 Prompt")
    new_prompt_name = st.text_input("新 Prompt 名称（用于保存）", value="", disabled=not access_granted, help="为要保存的系统 Prompt 输入一个唯一名字。", key="new_prompt_name")
    overwrite_existing = st.checkbox("若同名则覆盖已存在的 Prompt", value=False, disabled=not access_granted, key="overwrite_prompt")

    # 两个保存按钮：把当前自定义保存为系统 prompt；或把当前只读默认prompt另存为新条目
    save_custom_btn = st.button("把当前自定义保存为系统 Prompt", disabled=not access_granted)
    save_default_btn = st.button("把当前选中系统 Prompt 另存为新 Prompt", disabled=not access_granted)

    if save_custom_btn or save_default_btn:
        # 决定要保存的内容来源
        if save_custom_btn:
            content_to_save = st.session_state.get("system_prompt", "")
            if not content_to_save or not content_to_save.strip():
                st.warning("当前自定义 Prompt 为空，无法保存。请先在上方输入内容。")
            else:
                name = new_prompt_name.strip()
                if not name:
                    st.warning("请为新 Prompt 提供一个名称。")
                else:
                    success = sheet_db_prompts.save_prompt(name, content_to_save, overwrite=overwrite_existing)
                    msg = f"Prompt '{name}' 已保存到 Google Sheet。" if success else f"名为 '{name}' 的 prompt 已存在；如需覆盖请勾选覆盖选项。"
                    if success:
                        st.success(msg)
                        # 标记保存后要选中的 prompt，随后重跑页面以重新创建下拉控件
                        st.session_state["_select_after_save"] = name
                        # Attempt to rerun in a compatible way across Streamlit versions
                        rerun_fn = getattr(st, "experimental_rerun", None)
                        if callable(rerun_fn):
                            rerun_fn()
                        else:
                            try:
                                st.experimental_set_query_params(_prompt_saved=int(time.time()))
                                st.stop()
                            except Exception:
                                pass
                    else:
                        st.error(msg)
        else:
            # save_default_btn
            content_to_save = default_prompt_content
            if not content_to_save or not content_to_save.strip():
                st.warning("当前所选系统默认 Prompt 内容为空，无法保存为新条目。")
            else:
                name = new_prompt_name.strip()
                if not name:
                    st.warning("请为新 Prompt 提供一个名称。")
                else:
                    success = sheet_db_prompts.save_prompt(name, content_to_save, overwrite=overwrite_existing)
                    msg = f"Prompt '{name}' 已保存到 Google Sheet。" if success else f"名为 '{name}' 的 prompt 已存在；如需覆盖请勾选覆盖选项。"
                    if success:
                        st.success(msg)
                        st.session_state["_select_after_save"] = name
                        # Attempt to rerun in a compatible way across Streamlit versions
                        rerun_fn = getattr(st, "experimental_rerun", None)
                        if callable(rerun_fn):
                            rerun_fn()
                        else:
                            try:
                                st.experimental_set_query_params(_prompt_saved=int(time.time()))
                                st.stop()
                            except Exception:
                                pass
                    else:
                        st.error(msg)

    st.divider()
    st.markdown("""
    **支持平台：**
    - [atHome.lu](https://www.athome.lu)
    - [Wortimmo.lu](https://www.wortimmo.lu)
    - [Immotop.lu](https://www.immotop.lu)
    """)

# 主页面：输入与触发
col1, col2 = st.columns([5, 1])
with col1:
    url_input = st.text_input(
        "房源 URL 地址",
        placeholder="https://www.athome.lu/vente/appartement/...",
        disabled=not access_granted,
        label_visibility="collapsed"
    )
with col2:
    submit_btn = st.button("🚀 开始评估", use_container_width=True, type="primary", disabled=not access_granted)

if "report" not in st.session_state:
    st.session_state["report"] = ""
if "evaluation_error" not in st.session_state:
    st.session_state["evaluation_error"] = ""
# 持久化用户自定义 system prompt 到 session_state，页面刷新/重跑后保留
if "system_prompt" not in st.session_state:
    st.session_state["system_prompt"] = ""

# 触发评估逻辑
if access_granted and (submit_btn or (url_input and st.session_state.get("last_url") != url_input)):
    if not url_input.strip():
        st.warning("⚠️ 请先输入房源网址！")
    else:
        st.session_state["last_url"] = url_input
        st.session_state["report"] = ""
        st.session_state["evaluation_error"] = ""

        with st.status("🔍 正在分析房源数据...", expanded=True) as status:
            try:
                st.write("1. 正在提取网页正文与关键数据...")
                scraped_text = run_with_retry(
                    task_name="网页抓取",
                    func=lambda: extract_listing_content(url_input),
                    max_retries=2,
                    delay_seconds=1.5,
                )

                st.write(f"2. 正在调用 `{model_provider}` 进行深度评估...")
                # 根据侧边栏的选择决定使用自定义 prompt 还是系统默认 prompt
                if prompt_choice == "使用自定义提示词":
                    sp = st.session_state.get("system_prompt")
                    selected_prompt = sp.strip() if sp and sp.strip() else None
                else:
                    # 使用下拉选择的系统默认 prompt
                    selected_prompt = prompt_map.get(st.session_state.get("selected_prompt_name"))

                report = run_with_retry(
                    task_name="AI 评估",
                    func=lambda: evaluate_property(
                        model_name=model_provider,
                        listing_text=scraped_text,
                        custom_api_key=custom_api_key if custom_api_key.strip() else None,
                        system_prompt=selected_prompt
                    ),
                    max_retries=2,
                    delay_seconds=2.0,
                )

                status.update(label="✅ 评估完成！", state="complete", expanded=False)

                report_text = report if isinstance(report, str) else str(report or "")
                if not report_text.strip():
                    st.session_state["evaluation_error"] = "AI 未返回有效评估内容，请稍后重试或更换模型。"
                else:
                    st.session_state["report"] = report_text
            except Exception as ex:
                status.update(label="❌ 处理失败", state="error", expanded=True)
                st.session_state["evaluation_error"] = str(ex)

# 在处理逻辑外渲染，避免 Streamlit 下一次 rerun 时丢失结果。
if st.session_state["report"]:
    report_text = st.session_state["report"]
    st.divider()
    left, right = st.columns([1, 1])
    with left:
        if st.button("保存结果", use_container_width=True, disabled=not access_granted):
            try:
                sheet_db.append_evaluation(
                    url=url_input,
                    model_name=model_provider,
                    report=report_text,
                )
                st.success("已保存到 Google Sheet")
            except Exception as ex:
                st.error(f"保存到 Google Sheet 失败: {str(ex)}")
    with right:
        # 复制按钮：将结果复制到剪切板并给出提示
        copy_label = "复制结果"
        if st.button(copy_label, use_container_width=True, disabled=not access_granted):
            # 使用 st.iframe 嵌入一个 data URL 的小页面来执行复制动作（替代 components.html）
            safe_text = json.dumps(report_text)
            html = f"""
            <!doctype html>
            <html>
            <head>
              <meta charset='utf-8'>
              <meta name='viewport' content='width=device-width, initial-scale=1'>
              <title>复制</title>
              <style>
            .toast {{
              position: fixed;
              right: 20px;
              top: 20px;
              padding: 8px 12px;
              background: #E74C3C; /* only use for errors if shown */
              color: white;
              border-radius: 6px;
              z-index: 9999;
              font-family: sans-serif;
            }}
              </style>
            </head>
            <body>
            <script>
            function showError(text) {{
              const div = document.createElement('div');
              div.innerText = text;
              div.className = 'toast';
              document.body.appendChild(div);
              setTimeout(()=>div.remove(), 1800);
            }}
            (async () => {{
              const text = {safe_text};
              try {{
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              await navigator.clipboard.writeText(text);
            }} else {{
              // fallback for environments without navigator.clipboard
              const ta = document.createElement('textarea');
              ta.value = text;
              // Prevent zoom on iOS
              ta.style.position = 'fixed';
              ta.style.left = '-9999px';
              document.body.appendChild(ta);
              ta.focus();
              ta.select();
              const ok = document.execCommand('copy');
              ta.remove();
              if (!ok) throw new Error('execCommand(copy) failed');
            }}
            // success: do nothing (silent)
              }} catch (e) {{
            showError('复制失败: ' + (e && e.message ? e.message : e));
              }}
            }})();
            </script>
            </body>
            </html>
            """
            data_url = 'data:text/html;charset=utf-8,' + urllib.parse.quote(html)
            st.iframe(data_url, height=160)
    st.divider()
    st.markdown(report_text, unsafe_allow_html=True)

elif st.session_state["evaluation_error"]:
    st.error(f"错误详情：{st.session_state['evaluation_error']}")
    st.info("建议：\n- 检查 URL 是否正确\n- 检查 API Key 是否填写正确\n- 更换其他模型后重试\n- 若是网络问题，稍后再试")