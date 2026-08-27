# google_sheet_connector.py
from datetime import datetime
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection

class GoogleSheetConnector:
    """管理 Google Sheets 的连接、数据读取与追加"""

    def __init__(self, spreadsheet_url: str, worksheet: str = "Sheet1", connection_name: str = "gsheets"):
        self.spreadsheet_url = spreadsheet_url
        self.worksheet = worksheet
        # 初始化 Streamlit GSheets 连接
        self.conn = st.connection(connection_name, type=GSheetsConnection)

    def read_records(self, ttl: int = 0) -> pd.DataFrame:
        """读取表格中的所有记录
        
        :param ttl: 缓存时间（秒），设为 0 表示每次都从云端拉取最新数据
        :return: 包含历史记录的 DataFrame
        """
        try:
            df = self.conn.read(
                spreadsheet=self.spreadsheet_url,
                worksheet=self.worksheet,
                ttl=ttl
            )
            # 如果表格为空或全是空行，返回标准的空 DataFrame
            if df is None or df.empty:
                return pd.DataFrame(columns=["Date", "URL", "Model", "Report"])
            return df
        except Exception as e:
            # 表格刚创建完全空白时可能抛出异常，做容错处理
            return pd.DataFrame(columns=["Date", "URL", "Model", "Report"])

    def append_evaluation(self, url: str, model_name: str, report: str) -> bool:
        """向 Google Sheet 追加一条新的评估结果"""
        try:
            # 1. 读取现有数据
            existing_df = self.read_records(ttl=0)

            # 2. 构造新的一行
            new_entry = pd.DataFrame([{
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "URL": url,
                "Model": model_name,
                "Report": report
            }])

            # 3. 合并并更新回表格
            updated_df = pd.concat([existing_df, new_entry], ignore_index=True)
            self.conn.update(
                spreadsheet=self.spreadsheet_url,
                worksheet=self.worksheet,
                data=updated_df
            )
            return True
        except Exception as ex:
            raise RuntimeError(f"写入 Google Sheet 失败: {str(ex)}")

    def read_prompts(self, ttl: int = 0) -> pd.DataFrame:
        """读取 Prompt Sheet 中的 Name 和 Prompt 两列。"""
        try:
            df = self.conn.read(
                spreadsheet=self.spreadsheet_url,
                worksheet=self.worksheet,
                ttl=ttl,
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["Name", "Prompt"])
            return df[["Name", "Prompt"]].dropna(subset=["Name"])
        except Exception as ex:
            raise RuntimeError(f"读取 Prompt Google Sheet 失败: {str(ex)}") from ex

    def save_prompt(self, name: str, prompt: str, overwrite: bool = False) -> bool:
        """向 Prompt Sheet 保存一个 Prompt，必要时覆盖同名记录。"""
        existing_df = self.read_prompts(ttl=0)
        name_matches = existing_df["Name"].astype(str).str.strip() == name
        if name_matches.any() and not overwrite:
            return False

        if name_matches.any():
            existing_df.loc[name_matches, "Prompt"] = prompt
        else:
            new_entry = pd.DataFrame([{"Name": name, "Prompt": prompt}])
            existing_df = pd.concat([existing_df, new_entry], ignore_index=True)

        self.conn.update(
            spreadsheet=self.spreadsheet_url,
            worksheet=self.worksheet,
            data=existing_df[["Name", "Prompt"]],
        )
        return True