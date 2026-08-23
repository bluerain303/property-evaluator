# app.py
import time

import streamlit as st
from scraper import extract_listing_content
from llm_service import evaluate_property


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
        help="可临时手动填入对应的 API Key"
    )
    
    st.divider()
    st.markdown("""
    **支持平台：**
    - atHome.lu
    - Wortimmo.lu
    - Immotop.lu
    """)

# 主页面：输入与触发
col1, col2 = st.columns([5, 1])
with col1:
    url_input = st.text_input(
        "房源 URL 地址",
        placeholder="https://www.athome.lu/vente/appartement/...",
        label_visibility="collapsed"
    )
with col2:
    submit_btn = st.button("🚀 开始评估", use_container_width=True, type="primary")

if "report" not in st.session_state:
    st.session_state["report"] = ""
if "evaluation_error" not in st.session_state:
    st.session_state["evaluation_error"] = ""

# 触发评估逻辑
if submit_btn or (url_input and st.session_state.get("last_url") != url_input):
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
                report = run_with_retry(
                    task_name="AI 评估",
                    func=lambda: evaluate_property(
                        model_name=model_provider,
                        listing_text=scraped_text,
                        custom_api_key=custom_api_key if custom_api_key.strip() else None
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
    st.divider()
    st.markdown(st.session_state["report"], unsafe_allow_html=True)
elif st.session_state["evaluation_error"]:
    st.error(f"错误详情：{st.session_state['evaluation_error']}")
    st.info("建议：\n- 检查 URL 是否正确\n- 检查 API Key 是否填写正确\n- 更换其他模型后重试\n- 若是网络问题，稍后再试")