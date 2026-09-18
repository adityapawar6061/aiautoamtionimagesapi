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


def count_conversation_images(conversation_id: str) -> int:
    with connect_db() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM images WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return int(row["total"])


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


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        image_path = message.get("image_path")
        if image_path and Path(image_path).exists():
            st.image(image_path, width="stretch")


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


def generate_and_save_image(job: dict[str, Any]) -> None:
    """Generate one image for a scheduled job and persist everything.

    Called by the queue worker fragment, so the live progress (user prompt,
    spinner, result) renders inside the fragment's output area. The image and
    messages always go to the conversation the prompt was scheduled from,
    even if the user has switched to another one meanwhile.
    """
    conversation_id = job["conversation_id"]
    prompt = job["prompt"]
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Creating your image..."):
            try:
                image_bytes, extension = generate_image(
                    st.session_state.api_key or "",
                    prompt,
                    st.session_state.image_model,
                    st.session_state.image_size,
                )
                image_path = IMAGE_DIR / f"{conversation_id}-{uuid.uuid4().hex}.{extension}"
                image_path.write_bytes(image_bytes)
                response_text = "Created your image. It is saved with this conversation."
                user_message_id = add_message(conversation_id, "user", prompt)
                assistant_message_id = add_message(conversation_id, "assistant", response_text)
                add_image_record(conversation_id, assistant_message_id, prompt, str(image_path), st.session_state.image_model)
                if conversation_id == st.session_state.active_conversation_id:
                    st.session_state.messages.append({"id": user_message_id, "role": "user", "content": prompt})
                    st.session_state.messages.append(
                        {"id": assistant_message_id, "role": "assistant", "content": response_text, "image_path": str(image_path)}
                    )
                # Title the conversation after its very first image.
                if count_conversation_images(conversation_id) == 1:
                    update_conversation_title(conversation_id, prompt)
                st.markdown(response_text)
                st.image(str(image_path), width="stretch")
            except Exception as error:
                error_text = f"I could not create that image: {error}"
                assistant_message_id = add_message(conversation_id, "assistant", error_text)
                if conversation_id == st.session_state.active_conversation_id:
                    st.session_state.messages.append({"id": assistant_message_id, "role": "assistant", "content": error_text})
                st.error(error_text)


@st.fragment(run_every="3s")
def scheduled_prompt_worker() -> None:
    """Background worker: creates scheduled images strictly one after another.

    The fragment reruns on its own every few seconds, so the browser stays
    responsive while an image generates. Each run claims at most one prompt
    from the schedule, generates it to completion, then reruns the whole app,
    which claims the next one. Prompts therefore never overlap and never run
    in parallel — exactly one image is in progress at any moment.
    """
    queue = st.session_state.get("scheduled_prompts", [])
    job = st.session_state.get("current_job")
    if job is None and queue:
        st.session_state.current_job = queue.pop(0)
        st.session_state.scheduled_prompts = queue
        job = st.session_state.current_job
    if job is not None:
        if job.get("attempted"):
            # A previous run was interrupted mid-generation; skip the leftover
            # job instead of generating it twice.
            st.session_state.current_job = None
        else:
            job["attempted"] = True
            generate_and_save_image(job)
            st.session_state.current_job = None
        st.rerun(scope="app")
    render_queue_status()


def render_queue_status() -> None:
    """Small live status line showing the current job and waiting prompts."""
    queue = st.session_state.get("scheduled_prompts", [])
    job = st.session_state.get("current_job")
    lines: list[str] = []
    if job:
        preview = job["prompt"][:60] + ("..." if len(job["prompt"]) > 60 else "")
        waiting = len(queue)
        waiting_text = f" · {waiting} waiting" if waiting else ""
        lines.append(f":orange[:material/progress_circle:] Generating image: {preview}{waiting_text}")
    for index, item in enumerate(queue, start=1):
        preview = item["prompt"][:60] + ("..." if len(item["prompt"]) > 60 else "")
        lines.append(f":gray[**{index}.** {preview}]")
    if lines:
        st.caption("  \n".join(lines))


def add_scheduled_prompts() -> None:
    """Sidebar ＋ button callback: queue every non-empty line as one prompt.

    Runs before widgets are instantiated on the rerun, so clearing the text
    area's session key here is the supported way to reset the box.
    """
    batch = st.session_state.get("schedule_batch_text", "")
    prompts = [line.strip() for line in batch.splitlines() if line.strip()]
    if not (st.session_state.get("api_key") or "").strip():
        st.session_state.schedule_feedback = "Add your OpenAI API key in the sidebar first."
        return
    if not prompts:
        st.session_state.schedule_feedback = "Type at least one prompt — one prompt per line."
        return
    conversation_id = st.session_state.active_conversation_id
    st.session_state.scheduled_prompts = list(st.session_state.get("scheduled_prompts", [])) + [
        {"id": uuid.uuid4().hex, "prompt": prompt, "conversation_id": conversation_id}
        for prompt in prompts
    ]
    st.session_state.schedule_batch_text = ""
    st.session_state.schedule_feedback = (
        f"Scheduled {len(prompts)} prompt{'s' if len(prompts) != 1 else ''} — "
        "they will generate one after another, in order."
    )


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
        if st.button("+ New conversation"):
            conversation_id = create_conversation()
            load_conversation(conversation_id)
            st.rerun()

        st.markdown('<div class="history-label">History</div>', unsafe_allow_html=True)
        for conversation in list_conversations():
            label = conversation["title"] or "Untitled session"
            if st.button(label, key=f"conversation-{conversation['id']}"):
                load_conversation(conversation["id"])
                st.rerun()

        st.divider()

        # Schedule panel: paste many prompts (one per line) and press ＋ to
        # queue them all. The worker generates them one by one, in order.
        st.markdown('<div class="history-label">Schedule prompts</div>', unsafe_allow_html=True)
        st.text_area(
            "Prompts to schedule",
            height=120,
            key="schedule_batch_text",
            placeholder="One prompt per line, e.g.\nA neon koi pond at midnight\nA paper crane city above the clouds",
            help="Write one prompt per line. Every line becomes one scheduled image, generated in order.",
        )
        st.button("＋ Add to schedule", type="primary", width="stretch", on_click=add_scheduled_prompts)
        if "schedule_feedback" in st.session_state:
            st.caption(f":gray[{st.session_state.schedule_feedback}]")
            del st.session_state.schedule_feedback

        queue = st.session_state.get("scheduled_prompts", [])
        if queue:
            st.caption(f"{len(queue)} prompt{'s' if len(queue) != 1 else ''} waiting — generated one by one, in order.")
            for index, item in enumerate(queue):
                col_prompt, col_remove = st.columns([4, 1])
                preview = item["prompt"][:34] + ("..." if len(item["prompt"]) > 34 else "")
                col_prompt.markdown(f"**{index + 1}.** {preview}")
                if col_remove.button(":material/close:", key=f"remove-scheduled-{item['id']}", help="Remove this prompt from the schedule"):
                    queue.pop(index)
                    st.session_state.scheduled_prompts = queue
                    st.rerun()
            if st.button("Clear schedule", width="stretch"):
                st.session_state.scheduled_prompts = []
                st.rerun()
        else:
            st.caption("Nothing scheduled yet.")

        st.caption("Your key stays in session memory. Conversations and generated images stay in this app folder.")

    col1, col2 = st.columns([3, 1])
    with col2:
        st.selectbox("Image size", ["1024x1024", "1536x1024", "1024x1536"], key="image_size")
        st.selectbox("Image model", ["gpt-image-1"], key="image_model")
        st.caption("Every prompt creates one image, saved to the conversation that was active when you scheduled it.")

    with col1:
        for message in st.session_state.messages:
            render_message(message)
        if not st.session_state.messages:
            st.markdown('<div class="hint">Describe an image below, or schedule many prompts from the sidebar. Try: “A glass greenhouse on Mars at blue hour, editorial photography.”</div>', unsafe_allow_html=True)

        # Live queue status + one-by-one worker (refreshes itself).
        scheduled_prompt_worker()

        prompt = st.chat_input("Describe the image you want to create...")
        if prompt:
            if not api_key:
                st.error("Add your OpenAI API key in the sidebar before creating an image.")
                st.stop()

            # Every prompt goes onto the schedule; the worker generates them
            # one at a time, so the next image starts as soon as the first one
            # finishes — even if you keep typing more prompts.
            queue = st.session_state.get("scheduled_prompts", [])
            queue.append({"id": uuid.uuid4().hex, "prompt": prompt, "conversation_id": st.session_state.active_conversation_id})
            st.session_state.scheduled_prompts = queue
            st.rerun()


if __name__ == "__main__":
    main()
