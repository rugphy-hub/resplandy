"""Resplandy: streamlit run app.py. Agents propose, code checks, you decide."""
import streamlit as st

from resplandy.ui import common, literature_page, study_page

PAGES = {"Study": study_page.render, "Literature": literature_page.render}

st.set_page_config(page_title="Resplandy", layout="wide")
cfg = common.get_cfg()
conn = common.get_conn(cfg)
st.sidebar.title("Resplandy")
st.sidebar.caption("Research + planning + buddy")
page = st.sidebar.radio("Page", list(PAGES))
st.sidebar.caption(f"verification.mode: {cfg.verification.mode}" + ("  ·  REPLAY=1" if cfg.replay else ""))
PAGES[page](cfg, conn)
