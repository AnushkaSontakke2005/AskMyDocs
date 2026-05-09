from types import SimpleNamespace
from typing import Optional

import streamlit as st

from frontend.api_client import APP_BUILD_VERSION, ApiClientError, get, get_api_base_url, post


def _user_from_payload(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=payload["id"],
        email=payload["email"],
        is_admin=payload["is_admin"],
    )


def get_current_user() -> Optional[SimpleNamespace]:
    if "api_token" not in st.session_state:
        return None
    if "current_user" in st.session_state:
        return _user_from_payload(st.session_state["current_user"])

    try:
        user = get("/auth/me")
        st.session_state["current_user"] = user
        return _user_from_payload(user)
    except ApiClientError:
        st.session_state.pop("api_token", None)
        st.session_state.pop("current_user", None)
        return None


def require_login() -> SimpleNamespace:
    user = get_current_user()
    if not user:
        st.warning("Please log in to continue.")
        st.stop()
    return user


def render_login_page():
    st.title("AskMyDocs")
    st.caption("Log in to see only your documents, tags, and chat history.")

    with st.expander("Deployment debug", expanded=False):
        st.code(f"Build: {APP_BUILD_VERSION}\nAPI: {get_api_base_url()}")

    tab_login, tab_signup = st.tabs(["Login", "Create account"])

    with tab_login:
        email = st.text_input("Email", key="login-email")
        password = st.text_input("Password", type="password", key="login-password")
        if st.button("Login", type="primary"):
            try:
                token_response = post("/auth/login", {"email": email, "password": password})
                st.session_state["api_token"] = token_response["access_token"]
                st.session_state["current_user"] = get("/auth/me")
                st.rerun()
            except ApiClientError as exc:
                st.error(str(exc))

    with tab_signup:
        email = st.text_input("Email", key="signup-email")
        password = st.text_input("Password", type="password", key="signup-password")
        confirm_password = st.text_input("Confirm password", type="password", key="signup-confirm-password")
        if st.button("Create account"):
            if password != confirm_password:
                st.error("Passwords do not match.")
            else:
                try:
                    token_response = post("/auth/signup", {"email": email, "password": password})
                    st.session_state["api_token"] = token_response["access_token"]
                    st.session_state["current_user"] = get("/auth/me")
                    st.success("Account created.")
                    st.rerun()
                except ApiClientError as exc:
                    st.error(str(exc))


def render_sidebar_user():
    user = get_current_user()
    if not user:
        return
    st.sidebar.caption(f"Signed in as {user.email}")
    if user.is_admin:
        st.sidebar.caption("Role: Admin")
    if st.sidebar.button("Logout"):
        for key in ["api_token", "current_user", "messages", "selected_doc", "selected_doc_id", "chat_active"]:
            st.session_state.pop(key, None)
        st.rerun()
