from __future__ import annotations

import base64
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from openai import OpenAI
except ImportError:  # Allows the app to show its setup message before dependencies are installed.
    OpenAI = None


APP_DIR = Path(__file__).parent
DATABASE_PATH = APP_DIR / "image_chat.db"
IMAGE_DIR = APP_DIR / "generated_images"
IMAGE_DIR.mkdir(exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with connect_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                prompt TEXT NOT NULL,
                file_path TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def create_conversation(title: str = "New image session") -> str:
    conversation_id = str(uuid.uuid4())
    timestamp = now_iso()
    with connect_db() as connection:
        connection.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, title, timestamp, timestamp),
        )
    return conversation_id


def list_conversations() -> list[sqlite3.Row]:
    with connect_db() as connection:
        return connection.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()


def get_messages(conversation_id: str) -> list[sqlite3.Row]:
    with connect_db() as connection:
        return connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, id",
            (conversation_id,),
        ).fetchall()


def add_message(conversation_id: str, role: str, content: str) -> int:
    timestamp = now_iso()
    with connect_db() as connection:
        cursor = connection.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, timestamp),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (timestamp, conversation_id),
        )
        return int(cursor.lastrowid)


def update_conversation_title(conversation_id: str, title: str) -> None:
    with connect_db() as connection:
        connection.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title[:60], now_iso(), conversation_id),
        )


def add_image_record(
    conversation_id: str,
    message_id: int,
    prompt: str,
    file_path: str,
    model: str,
) -> None:
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO images (conversation_id, message_id, prompt, file_path, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, message_id, prompt, file_path, model, now_iso()),
        )


def load_conversation(conversation_id: str) -> None:
    st.session_state.active_conversation_id = conversation_id
    st.session_state.messages = [dict(message) for message in get_messages(conversation_id)]


def ensure_active_conversation() -> None:
    if "active_conversation_id" not in st.session_state:
        conversations = list_conversations()
        conversation_id = conversations[0]["id"] if conversations else create_conversation()
        load_conversation(conversation_id)


def generate_image(api_key: str, prompt: str, model: str, size: str) -> tuple[bytes, str]:
    if OpenAI is None:
        raise RuntimeError("The OpenAI package is not installed. Run: pip install -r requirements.txt")
    client = OpenAI(api_key=api_key)
    result = client.images.generate(model=model, prompt=prompt, size=size)
    image_data: Any = result.data[0]
    encoded = getattr(image_data, "b64_json", None)
    if not encoded:
        raise RuntimeError("The image API returned no image data.")
    return base64.b64decode(encoded), "png"


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("image_path") and Path(message["image_path"]).exists():
            st.image(message["image_path"], use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Canvas Chat", page_icon="C", layout="wide")
    initialize_database()
    ensure_active_conversation()

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root { --ink: #17211b; --muted: #68736b; --mint: #dcefe4; --accent: #e26d45; --line: #dfe7e1; }
        html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
        h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; color: var(--ink); }
        .hero { padding: 0.5rem 0 1.5rem; border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; }
        .hero-kicker { color: var(--accent); font-size: .78rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
        .hero h1 { font-size: clamp(2rem, 4vw, 3.4rem); line-height: 1; margin: .35rem 0 .65rem; }
        .hero p { color: var(--muted); max-width: 620px; margin: 0; font-size: 1.05rem; }
        [data-testid="stSidebar"] { background: #f3f6f2; border-right: 1px solid var(--line); }
        .history-label { color: var(--muted); font-size: .75rem; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; margin: 1.25rem 0 .55rem; }
        .stButton > button { border-radius: 8px; border-color: var(--line); }
        .stChatMessage { border: 1px solid var(--line); border-radius: 12px; padding: .75rem 1rem; }
        .hint { color: var(--muted); font-size: .9rem; padding: 1rem 0 2rem; }
        </style>
        <div class="hero">
          <div class="hero-kicker">Canvas / image studio</div>
          <h1>Think it. Type it. See it.</h1>
          <p>A private image-making chat with searchable conversations and a complete local history.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### Canvas Chat")
        api_key = st.text_input("OpenAI API key", type="password", value=st.session_state.get("api_key", ""), help="Used only for this browser session; it is not written to the database.")
        st.session_state.api_key = api_key
        st.divider()
        if st.button("+ New conversation", use_container_width=True):
            conversation_id = create_conversation()
            load_conversation(conversation_id)
            st.rerun()

        st.markdown('<div class="history-label">History</div>', unsafe_allow_html=True)
        for conversation in list_conversations():
            label = conversation["title"] or "Untitled session"
            if st.button(label, key=f"conversation-{conversation['id']}", use_container_width=True):
                load_conversation(conversation["id"])
                st.rerun()

        st.divider()
        st.caption("Your key stays in session memory. Conversations and generated images stay in this app folder.")

    col1, col2 = st.columns([3, 1])
    with col2:
        st.selectbox("Image size", ["1024x1024", "1536x1024", "1024x1536"], key="image_size")
        st.selectbox("Image model", ["gpt-image-1"], key="image_model")
        st.caption("Each prompt creates an image and is saved to the active conversation.")

    with col1:
        for message in st.session_state.messages:
            render_message(message)
        if not st.session_state.messages:
            st.markdown('<div class="hint">Describe an image below. Try: “A glass greenhouse on Mars at blue hour, editorial photography.”</div>', unsafe_allow_html=True)

        prompt = st.chat_input("Describe the image you want to create...")
        if prompt:
            if not api_key:
                st.error("Add your OpenAI API key in the sidebar before creating an image.")
                st.stop()

            user_message_id = add_message(st.session_state.active_conversation_id, "user", prompt)
            user_message = {"id": user_message_id, "role": "user", "content": prompt}
            st.session_state.messages.append(user_message)
            render_message(user_message)

            with st.chat_message("assistant"):
                with st.spinner("Creating your image..."):
                    try:
                        image_bytes, extension = generate_image(api_key, prompt, st.session_state.image_model, st.session_state.image_size)
                        image_path = IMAGE_DIR / f"{st.session_state.active_conversation_id}-{uuid.uuid4().hex}.{extension}"
                        image_path.write_bytes(image_bytes)
                        response_text = "Created your image. It is saved with this conversation."
                        assistant_message_id = add_message(st.session_state.active_conversation_id, "assistant", response_text)
                        add_image_record(st.session_state.active_conversation_id, assistant_message_id, prompt, str(image_path), st.session_state.image_model)
                        assistant_message = {"id": assistant_message_id, "role": "assistant", "content": response_text, "image_path": str(image_path)}
                        st.session_state.messages.append(assistant_message)
                        if len(st.session_state.messages) == 2:
                            update_conversation_title(st.session_state.active_conversation_id, prompt)
                        st.markdown(response_text)
                        st.image(str(image_path), use_container_width=True)
                    except Exception as error:
                        error_text = f"I could not create that image: {error}"
                        assistant_message_id = add_message(st.session_state.active_conversation_id, "assistant", error_text)
                        st.session_state.messages.append({"id": assistant_message_id, "role": "assistant", "content": error_text})
                        st.error(error_text)


if __name__ == "__main__":
    main()
