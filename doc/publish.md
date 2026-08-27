将本地写好的 Streamlit 应用部署到**免费的 Streamlit Community Cloud** 上，总共需要四个主要阶段：准备文件、上传到 GitHub、在 Streamlit Cloud 部署，以及最重要的配置云端密钥。

以下是保姆级的详细部署步骤：

### 第一阶段：准备本地部署文件

在将代码上传到云端之前，必须告诉云端服务器需要安装哪些第三方包，同时还要**严防你的私钥被泄露**。

1. **创建依赖文件 `requirements.txt**`
在你的项目根目录（与 `app.py` 同级）新建一个文件，命名为 `requirements.txt`，填入你项目用到的所有核心库：
```text
streamlit
litellm
requests
beautifulsoup4
st-gsheets-connection
pandas

```


2. **创建 Git 忽略文件 `.gitignore`（⚠️ 极其重要）**
如果你要把代码传到公开的 GitHub 仓库，**绝对不能把本地的密钥传上去**。
在根目录新建一个文件，命名为 `.gitignore`（注意前面有个点），填入以下内容：
```text
# 忽略虚拟环境
venv/
env/
.env

# 忽略 Streamlit 本地密钥文件夹
.streamlit/

# 忽略 Python 缓存文件
__pycache__/

```



### 第二阶段：将代码上传到 GitHub

Streamlit Cloud 必须通过读取你的 GitHub 仓库来部署代码。

1. 登录你的 [GitHub 账号](https://github.com/)。
2. 点击右上角的 **"+"**，选择 **"New repository"**。
3. 给仓库起个名字（例如 `lux-property-evaluator`），设为 **Public**（公开）或 **Private**（私有均可，Streamlit 支持读取私有仓库），然后点击 **"Create repository"**。
4. **最简单的方法（无需命令行）**：
在新建的仓库页面上，点击 **"uploading an existing file"**，把你的 `app.py`、`scraper.py`、`llm_service.py`、`google_sheet_connector.py` 和刚刚写的 `requirements.txt`、`.gitignore` 一起全选，拖拽到网页里上传，然后点击 **"Commit changes"**。

### 第三阶段：在 Streamlit Cloud 创建应用

1. 打开并登录 [Streamlit Community Cloud](https://share.streamlit.io/)（可以使用你的 GitHub 账号一键授权登录）。
2. 点击右上角的 **"New app"** 按钮。
3. 选择 **"Use existing repo"**（使用现有仓库）。
4. 依次填写以下信息：
* **Repository**: 填你刚刚创建的 GitHub 仓库名（例如 `你的用户名/lux-property-evaluator`）。
* **Branch**: 通常默认是 `main` 或 `master`。
* **Main file path**: 填 `app.py`。
* **App URL**: 你可以自定义一个好记的专属网址后缀（如果没被别人占用的话）。


5. **⚠️ 慢着，先别点 Deploy！点左下角的 "Advanced settings"（高级设置）。**

### 第四阶段：配置云端 Secrets（打通 Google Sheets）

还记得我们在本地放在 `.streamlit/secrets.toml` 里的那些密钥吗？现在要把它们贴给云端服务器。

1. 在刚刚打开的 **"Advanced settings"** 面板中，你会看到一个名为 **"Secrets"** 的巨大文本框。
2. 回到你电脑本地，打开 `.streamlit/secrets.toml` 或 `.env` 文件。
3. 把里面的全部内容原封不动地复制，粘贴到网页上的这个 **Secrets** 文本框里。
*(它看起来应该像这样：)*
```toml
[connections.gsheets]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
# ... 其他 google sheets 参数

# 如果你之前在 .env 里放了 Gemini 或大模型的 API Key，也写在文件最上面：
GEMINI_API_KEY = "your_api_key_here"

```


4. 点击 **"Save"** 保存设置。
5. 最后，点击右下角的 **"Deploy"** 按钮！

**🚀 接下来：泡杯咖啡，等待上线**
屏幕上会出现一个带着烤箱图标的加载画面（通常需要 1-3 分钟，因为服务器在后台安装你的 `requirements.txt` 里的包）。一旦加载完成，你的网页就会正式显示出来。

你可以把部署成功后浏览器里的 URL 收藏到手机上，或者发给家人。以后在外面看到好的房源，随时随地手机点开就能一键生成评估报告并存入云端表格了。