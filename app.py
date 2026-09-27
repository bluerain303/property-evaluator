import hmac
import os
import time

import streamlit as st
from scraper import extract_listing_content
from llm_service import evaluate_property
import json
import urllib.parse
import pandas as pd
from google_sheet_connector import GoogleSheetConnector
from search.athome_search import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_PAGES,
    SearchFilters,
    search_athome,
)

SEARCH_DISPLAY_COLUMNS = [
    "listing_id",
    "title",
    "price",
    "surface_m2",
    "price_per_m2",
    "bedrooms",
    "bathrooms",
    "energy_class",
    "thermal_insulation_class",
    "is_new_build",
    "listing_url",
    "map_url",
    "city",
    "postal_code",
    "country",
    "latitude",
    "longitude",
    "photo_count",
    "page",
    "toilets",
    "parking_spaces",
    "address",
    "year_built",
    "property_floor",
    "description",
]
ATHOME_AREA_CODES = {
    "L2-luxembourg": "全卢森堡",
    "L4-centre": "中部",
    "L4-sud": "南部",
    "L9-luxembourg": "卢森堡区域",
}
LUXEMBOURG_CITIES = [
    "Bascharage",
    "Bettembourg",
    "Bertrange",
    "Clervaux",
    "Differdange",
    "Diekirch",
    "Dudelange",
    "Echternach",
    "Esch-sur-Alzette",
    "Ettelbruck",
    "Grevenmacher",
    "Hesperange",
    "Junglinster",
    "Luxembourg",
    "Luxembourg-Merl",
    "Mamer",
    "Mersch",
    "Mondorf-les-Bains",
    "Pétange",
    "Remich",
    "Sandweiler",
    "Sanem",
    "Schifflange",
    "Steinsel",
    "Strassen",
    "Walferdange",
    "Wiltz",
]


def build_google_maps_url(latitude, longitude):
    if pd.isna(latitude) or pd.isna(longitude):
        return None
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


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

    expected_passcode = os.getenv("PASSCODE", "")

    if "passcode_verified" not in st.session_state:
        st.session_state["passcode_verified"] = False
    if "passcode_checked_value" not in st.session_state:
        st.session_state["passcode_checked_value"] = ""

    passcode_col, verify_col = st.columns([4, 1.3], vertical_alignment="center")
    with passcode_col:
        passcode = st.text_input(
            "访问口令",
            type="password",
            help="请输入访问口令"
        )

    with verify_col:
        verify_passcode = st.button("校验", use_container_width=True)

    if passcode != st.session_state.get("passcode_checked_value", ""):
        st.session_state["passcode_verified"] = False

    if verify_passcode:
        st.session_state["passcode_checked_value"] = passcode
        st.session_state["passcode_verified"] = bool(expected_passcode) and hmac.compare_digest(passcode, expected_passcode)
        if st.session_state["passcode_verified"]:
            st.success("✅ 口令正确")
        else:
            st.warning("❌ 口令不正确")

    access_granted = bool(expected_passcode) and bool(st.session_state.get("passcode_verified", False))
    if passcode and not access_granted:
        st.caption("请先点击“校验”确认访问口令。")

    st.header("⚙️ 模型配置")
    
    model_provider = st.selectbox(
        "选择 AI 模型",
        options=[
            "gemini/gemini-3.6-flash",
            "deepseek/deepseek-v4-flash",
            "deepseek/deepseek-v4-pro"
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

evaluation_tab, search_tab = st.tabs(["房源评估", "房源搜索"])

with evaluation_tab:
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

with search_tab:
    st.subheader("atHome.lu 房源搜索")
    st.caption("设置搜索条件后提交；结果会保留在本页，直到下一次搜索。")
    search_defaults = SearchFilters()

    if "property_search_results" not in st.session_state:
        st.session_state["property_search_results"] = None
    if "property_search_error" not in st.session_state:
        st.session_state["property_search_error"] = ""

    if not st.session_state.get("_search_numeric_defaults_initialized"):
        for key, default_value in {
            "search_price_min": search_defaults.price_min,
            "search_price_max": search_defaults.price_max,
            "search_surface_min": search_defaults.surface_min,
            "search_bedrooms_min": search_defaults.bedrooms_min,
            "search_bedrooms_max": search_defaults.bedrooms_max,
        }.items():
            if st.session_state.get(key) is None:
                st.session_state[key] = default_value
        st.session_state["_search_numeric_defaults_initialized"] = True

    with st.expander("搜索参数", expanded=False):
        transaction_type = st.selectbox(
            "交易类型",
            ["buy", "rent"],
            index=["buy", "rent"].index(search_defaults.transaction_type),
            format_func=lambda value: "购买" if value == "buy" else "租赁",
        )
        property_types = st.multiselect(
            "房产类型",
            ["flat", "house", "new-property"],
            default=search_defaults.property_types,
            format_func=lambda value: {"flat": "公寓", "house": "房屋", "new-property": "新建房产"}[value],
        )

        price_col1, price_col2 = st.columns(2)
        with price_col1:
            price_min = st.number_input(
                "最低价格 (EUR)", min_value=0,
                step=10000, key="search_price_min"
            )
        with price_col2:
            price_max = st.number_input(
                "最高价格 (EUR)", min_value=0,
                step=10000, key="search_price_max"
            )

        surface_col1, surface_col2 = st.columns(2)
        with surface_col1:
            surface_min = st.number_input(
                "最小面积 (m²)", min_value=0, step=5, key="search_surface_min"
            )
        with surface_col2:
            surface_max = st.number_input(
                "最大面积 (m²，可选)", min_value=0, value=search_defaults.surface_max,
                step=5, key="search_surface_max"
            )

        bedrooms_col1, bedrooms_col2 = st.columns(2)
        with bedrooms_col1:
            bedrooms_min = st.number_input(
                "最少卧室数", min_value=0, step=1, key="search_bedrooms_min"
            )
        with bedrooms_col2:
            bedrooms_max = st.number_input(
                "最多卧室数", min_value=0, step=1, key="search_bedrooms_max"
            )

        default_area_codes = (
            [code.strip() for code in search_defaults.loc.split(",") if code.strip()]
            if search_defaults.loc
            else []
        )
        area_codes = st.multiselect(
            "atHome 地区",
            options=list(ATHOME_AREA_CODES),
            default=default_area_codes,
            format_func=lambda code: f"{ATHOME_AREA_CODES[code]} ({code})",
            key="search_area_codes",
            help="可以多选；列表使用项目参考中记录的 atHome 地区代码。",
        )
        previous_results = st.session_state.get("property_search_results")
        observed_cities = (
            previous_results["city"].dropna().astype(str).str.strip().tolist()
            if previous_results is not None and "city" in previous_results.columns
            else []
        )
        city_options = sorted(set(LUXEMBOURG_CITIES).union(
            city for city in observed_cities if city
        ), key=str.casefold)
        selected_cities = st.multiselect(
            "城市 / 市镇",
            options=city_options,
            key="search_cities",
            help="可多选；搜索结果中出现的新城市也会加入选项。",
        )

        sort_options = ["date_desc", "price_asc", "price_desc", "srf_desc"]
        sort_by = st.selectbox(
            "排序方式",
            sort_options,
            index=sort_options.index(search_defaults.sort_by),
            format_func=lambda value: {
                "date_desc": "最新发布",
                "price_asc": "价格从低到高",
                "price_desc": "价格从高到低",
                "srf_desc": "面积从大到小",
            }[value],
        )
        exclude_borders = st.checkbox(
            "排除卢森堡以外的房源", value=search_defaults.exclude_borders
        )
        exclude_price_on_request = st.checkbox(
            "排除未标明价格的房源", value=search_defaults.exclude_price_on_request
        )
        max_pages = st.number_input(
            "抓取页数", min_value=1, max_value=20, value=DEFAULT_MAX_PAGES, step=1
        )
        delay_seconds = st.number_input(
            "分页间隔 (秒)", min_value=0.0, value=DEFAULT_DELAY_SECONDS, step=0.5
        )

    search_submitted = st.button(
        "搜索房源",
        type="primary",
        disabled=not access_granted,
        use_container_width=True,
    )

    if not access_granted:
        st.info("请先在侧边栏验证访问口令，再使用房源搜索。")

    if search_submitted:
        range_errors = []
        for minimum, maximum, label in (
            (price_min, price_max, "价格"),
            (surface_min, surface_max, "面积"),
            (bedrooms_min, bedrooms_max, "卧室数量"),
        ):
            if minimum is not None and maximum is not None and minimum > maximum:
                range_errors.append(f"{label}的最小值不能大于最大值。")

        if range_errors:
            st.session_state["property_search_error"] = " ".join(range_errors)
        else:
            filters = SearchFilters(
                transaction_type=transaction_type,
                property_types=property_types,
                price_min=price_min,
                price_max=price_max,
                surface_min=surface_min,
                surface_max=surface_max,
                bedrooms_min=bedrooms_min,
                bedrooms_max=bedrooms_max,
                exclude_borders=exclude_borders,
                sort_by=sort_by,
                loc=",".join(area_codes) or None,
                cities=selected_cities or None,
                exclude_price_on_request=exclude_price_on_request,
            )
            st.session_state["property_search_error"] = ""
            try:
                with st.spinner("正在向 atHome.lu 请求房源..."):
                    st.session_state["property_search_results"] = search_athome(
                        filters,
                        max_pages=int(max_pages),
                        delay_seconds=float(delay_seconds),
                    )
            except Exception as ex:
                st.session_state["property_search_results"] = None
                st.session_state["property_search_error"] = str(ex)

    if st.session_state["property_search_error"]:
        st.error(f"搜索失败：{st.session_state['property_search_error']}")

    search_results = st.session_state["property_search_results"]
    if search_results is not None:
        st.write(f"共找到 {len(search_results)} 条房源。")
        display_results = search_results.copy()
        display_results["map_url"] = [
            build_google_maps_url(row.get("latitude"), row.get("longitude"))
            for _, row in display_results.iterrows()
        ]
        visible_columns = [
            column for column in SEARCH_DISPLAY_COLUMNS
            if column in display_results.columns
        ]
        st.dataframe(
            display_results.loc[:, visible_columns],
            column_config={
                "listing_url": st.column_config.LinkColumn(
                    "URL",
                    display_text="Link",
                ),
                "map_url": st.column_config.LinkColumn(
                    "Map",
                    display_text="Map",
                ),
            },
            width="stretch",
            hide_index=True,
            height=600,
        )