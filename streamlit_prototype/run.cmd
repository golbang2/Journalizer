@echo off
cd /d "%~dp0.."
python -m streamlit run streamlit_prototype/app.py --server.address 127.0.0.1
