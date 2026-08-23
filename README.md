# property-evaluator
a website to evaluate provided property information

# 2026-08-23 ==============
1. Create python venv
    command: python3 -m venv venv-pe

2. Activate the environment: 
    command: source venv/bin/activate

3. Upgrade pip: 
    command: python3 -m pip install --upgrade pip

4. Intall libs:
    command: pip install streamlit litellm requests beautifulsoup4 python-dotenv
    
    note: python-dotenv is for .env file storing the API key. beautifulsoup4 helps to clean up the downloaded website.

5. Freeze Requirements: 
    note: This saves a list of your libraries so you can reinstall them later easily.
    command: pip freeze > requirements.txt

6. Create the .env file and make sure it is in the gitignore list. Put the API key in the .env file.

To start the website
streamlit run app.py
# =========================